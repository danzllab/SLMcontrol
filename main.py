# -*- coding: utf-8 -*-
"""
Created on Fri Oct 21 10:22:25 2016

@author: wjahr

TODO:
+ set checkboxes according to params files upon startup
+ Load flat field correction according to checkbox
+ make sure that not only 1st aberration is loaded correctly from params
+ code sngl aberration case
- code split image
+ checkbox format when exporting to JSON
+ load image from file for vortex
+ scale according to wavelength
+ code bivortex
+ code segmented phase plate out of half moon
- save paths for corrections etc correctly when saving params (hardcoded atm)
"""
# standard imports
import sys, os

# third party imports 
import PyQt5.QtCore as QtCore
import PyQt5.QtWidgets as QtWidgets
from PyQt5.QtGui import QPixmap, QImage
try:
    import specpy as sp
except:
    print("Specpy not installed!")
    pass

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import json

#from PIL import Image

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
#import scipy
#import scipy.ndimage 

# local packages
import slm_control.Pattern_Calculator as pcalc
import slm_control.Pattern_Interface as PI
import slm_control.Patterns_Zernike as PZ
import slm_control.SLM as SLM

from slm_control.Parameters import param

#sys.path.insert(1, os.getcwd())
#sys.path.insert(1, 'slm_control/')
#sys.path.insert(1, 'autoalign/')
import microscope  
import autoalign.utils.helpers as helpers

mpl.rc('text', usetex=False)
mpl.rc('font', family='serif')
mpl.rc('pdf', fonttype=42)


class PlotCanvas(FigureCanvas):
    """ Provides a matplotlib canvas to be embedded into the widgets. "Native"
        matplotlib.pyplot doesn't work because it interferes with the Qt5
        framework. Plot function of this class takes the data passed as an
        argument and plots via imshow(). Handy for testing things, because
        the QPixmap automatically phasewraps intensities into the space between
        [0,1], which might interfere with the phasewrapping implemented for 
        the SLM. """
        
    def __init__(self, parent=None, w=800, h=600, dpi=200):
        w = w / dpi
        h = h / dpi
        fig = Figure(figsize=(w,h), dpi=dpi)
        self.img_ax = fig.add_subplot(111)
        self.img_ax.set_xticks([]), self.img_ax.set_yticks([])
        
        FigureCanvas.__init__(self, fig)
        self.setParent(parent)
 
    def plot(self, data):
        self.img_ax.imshow(data, interpolation = 'nearest', clim = [0,1], cmap = 'RdYlBu')#'PRGn')
        self.draw()


class Main_Window(QtWidgets.QMainWindow):
    """ Main window for SLM control. Controls to change the parameters, and all
        function calls. """

    def __init__(self, app, parent=None):
        """ Called upon start up of the class. Initializes the Gui, places all
            windows. Loads the parameters from file and initializes the 
            patterns with the parameter sets loaded from the files."""
        QtWidgets.QMainWindow.__init__(self, parent)        
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        
        self.setWindowTitle('Main Window')
        self.app = app
        self.slm = None
        
        screen0 = QtWidgets.QDesktopWidget().screenGeometry()
        self.setGeometry(screen0.left(), screen0.top(), 
                         screen0.width()/4, .9*screen0.height())
        
        self.param_path = ['parameters/', 'params']
        self.p = param()
        self.p.load_file_general(self.param_path[0], self.param_path[1])
        self.current_objective = self.p.general["objective"]
        self.p.load_file_obj(self.param_path[0], self.current_objective, self.param_path[1])
        self.p.load_file_sim(self.param_path[0], self.param_path[1])
        
        self.slm_radius = self.calc_slmradius(
            self.p.objectives[self.current_objective]["backaperture"],
            self.p.general["slm_mag"])
        
        self.init_data()
        self.show()
        self.raise_()
        
        
    def init_data(self):
        """ Called upon start up to initialize all the date for the first time.
            Recalled when the split_image checkbox is changed, because this
            will change the size of all the images etc. """        
        
        if self.p.general["split_image"]:
            self.img_size = np.asarray(self.p.general["size_slm"])
            self.load_flat_field(self.p.left["cal1"], self.p.right["cal1"], recalc = False)
        else:
            self.img_size = np.asarray([self.p.general["size_slm"][0], 
                                        self.p.general["size_slm"][1]*2])
            self.load_flat_field(self.p.general["cal1"], self.p.general["cal1"], recalc = False)
        
        self.init_zernikes()
        self.init_images()
        self.create_main_frame()
        self.combine_and_update()
        self.groundtruth = None
        

    def reload_params(self, fname):
        """ Calls the load_file function implemented in the parameters class, 
            which loads the parameter lists from the text file. Called after
            button is clicked. """
        self.p.load_file_obj(fname[0], self.current_objective, fname[1])
        print("params ", self.p.full["mode"])

        if self.p.general["split_image"]:
            print("loading from file for split image")
            self.img_l.update_guivalues(self.p, self.p.left)
            self.img_r.update_guivalues(self.p, self.p.right)
            self.phase_zern = np.zeros_like(self.img_l.data)
            self.phase_tiptilt = np.zeros_like(self.img_l.data)
            self.phase_defocus = np.zeros_like(self.img_l.data)
        else:
            print("loading from file for full image")
            self.img_full.update_guivalues(self.p, self.p.full)
            self.phase_zern = np.zeros_like(self.img_full.data)
            self.phase_tiptilt = np.zeros_like(self.img_full.data)
            self.phase_defocus = np.zeros_like(self.img_full.data)
        
        self.recalc_images()

        
    def save_params(self, fname):
        """" Calls write_file implemented in parameters class to save the 
            current parameters to the file provided in fname. These are then 
            loaded as default on next startup."""
        self.p.update(self)
        self.p.write_file(fname[0], self.current_objective, fname[1])
        
        
    def init_images(self):
        """ Called upon startup of the program. Initizialises the variables
            containing the left and right halves of the SLM or the full image,
            depending on the state of the "split image" boolean. """
        
        if self.p.general["split_image"]:
            
            self.img_l = PI.Half_Pattern(self.p, self.img_size)
            self.img_l.call_daddy(self)
            self.img_l.set_name("img_l")
            
            self.img_r = PI.Half_Pattern(self.p, self.img_size)
            self.img_r.call_daddy(self)
            self.img_r.set_name("img_r")
    
            self.phase_zern = np.zeros_like(self.img_l.data)
            self.phase_tiptilt = np.zeros_like(self.img_l.data)
            self.phase_defocus = np.zeros_like(self.img_l.data)
            
        else:
            self.img_full = PI.Half_Pattern(self.p, self.img_size)
            self.img_full.call_daddy(self)
            self.img_full.set_name("full")
            self.phase_zern = np.zeros_like(self.img_full.data)
            self.phase_tiptilt = np.zeros_like(self.img_full.data)
            self.phase_defocus = np.zeros_like(self.img_full.data)
        
        
    def init_zernikes(self):
        """ Creates a dictionary containing all of the Zernike polynomials by
            their names. Updates in the GUI only change the weight of each 
            polynomial. Thus, polynomials do not have to be updated, just their
            weights. """
            
        # normalisation for tip / tilt is different from the other Zernikes:
        # grating periods should be in /mm and they should be independent of
        # the objective's diameter:
        # we're using the SLM off-axis to reflect the beam into the center of
        # the objective's backaperture. None of the actual optics depends on the
        # objective used.
        # I'm using the SLM radius calculation with backaperture = mag = 1
        # to determine correct size for tip/tilt. Extra factor of two is because
        # patterns are created at double size, then cropped.

        self.rtiptilt = 2 * pcalc.normalize_radius(1, 1, self.p.general["slm_px"], 
                                                    self.p.general["size_slm"])
        
        self.zernikes_normalized = {
            "tiptiltx" : pcalc.create_zernike(2 * self.img_size, [ 1,  1], 1, self.rtiptilt),
            "tiptilty" : pcalc.create_zernike(2 * self.img_size, [ 1, -1], 1, self.rtiptilt),
            "defocus"  : pcalc.create_zernike(2 * self.img_size, [ 2,  0], 1, self.slm_radius),
            "astigx"   : pcalc.create_zernike(2 * self.img_size, [ 2,  2], 1, self.slm_radius),
            "astigy"   : pcalc.create_zernike(2 * self.img_size, [ 2, -2], 1, self.slm_radius),
            "comax"    : pcalc.create_zernike(2 * self.img_size, [ 3,  1], 1, self.slm_radius),
            "comay"    : pcalc.create_zernike(2 * self.img_size, [ 3, -1], 1, self.slm_radius),
            "trefoilx" : pcalc.create_zernike(2 * self.img_size, [ 3,  3], 1, self.slm_radius),
            "trefoily" : pcalc.create_zernike(2 * self.img_size, [ 3, -3], 1, self.slm_radius),           
            "sphere1"  : pcalc.create_zernike(2 * self.img_size, [ 4,  0], 1, self.slm_radius),
            "sphere2"  : pcalc.create_zernike(2 * self.img_size, [ 6,  0], 1, self.slm_radius)
            }
    
        
    def create_main_frame(self):
        """ Creates the UI: Buttons to load/save parameters and flatfield 
            correction. Frames to display the patterns. Creates the GUI 
            elements contained in the Half_Pattern and Aberr_Pattern classes 
            that are used to change the parameters for pattern creation. """
        
        self.main_frame = QtWidgets.QWidget()  
        vbox = QtWidgets.QVBoxLayout()     

        # Quit, initialize, close slm buttons
        hbox = QtWidgets.QHBoxLayout()
        self.crea_but(hbox, self._quit, "Quit")       
        self.crea_but(hbox, self.open_SLMDisplay, "Initialize SLM")
        self.crea_but(hbox, self.close_SLMDisplay, "Close SLM")
        hbox.setContentsMargins(0,0,0,0)
        vbox.addLayout(hbox)         

        # controls to change objectives and to load/save calibration files         
        hbox = QtWidgets.QHBoxLayout()
        self.obj_sel = QtWidgets.QComboBox(self)
        self.obj_sel.setMaximumSize(100, 50)
        hbox.addWidget(self.obj_sel)            
        for mm in self.p.objectives:
            self.obj_sel.addItem(mm)
        self.obj_sel.setCurrentText(self.current_objective)
        self.obj_sel.activated.connect(lambda: self.objective_changed())
        
        self.rad_but = QtWidgets.QDoubleSpinBox()
        self.rad_but.setDecimals(3)
        self.rad_but.setSingleStep(0.01)
        self.rad_but.setMinimum(0.01)
        self.rad_but.setMaximum(10)
        self.rad_but.setValue(1.68)
        self.rad_but.setMaximumSize(80,50)
        self.rad_but.valueChanged.connect(lambda: self.radius_changed())
        hbox.addWidget(self.rad_but)
        
        self.crea_but(hbox, self.reload_params, "Load Config", self.param_path)
        self.crea_but(hbox, self.save_params, "Save Config", self.param_path)
        hbox.setContentsMargins(0,0,0,0)
        vbox.addLayout(hbox)
        
        # controls for autoalignment
        hbox = QtWidgets.QHBoxLayout()
        self.crea_but(hbox, self.auto_align, "Auto Align")
        self.crea_but(hbox, self.automate, "Auto-test")
        hbox.setContentsMargins(0,0,0,0)
        vbox.addLayout(hbox)

        # checkboxes for the different modes of operation: flatfield correction
        # and single correction and cross correction for double pass geometry
        # (as on the Abberior))
        hbox = QtWidgets.QHBoxLayout()
        self.sngl_corr_state = self.crea_checkbox(hbox, self.single_correction, 
                        "Single correction", self.p.general["single_aberr"])
        self.dbl_pass_state = self.crea_checkbox(hbox, self.double_pass,
                        "Double pass", self.p.general["double_pass"])
        self.flt_fld_state = self.crea_checkbox(hbox, self.flat_field, 
                        "Flatfield", self.p.general["flat_field"])
        hbox.setContentsMargins(0,0,0,0)
        vbox.addLayout(hbox)
        
        # creates the Widget to display the image that's being sent to the SLM
        imgbox = QtWidgets.QHBoxLayout()
        imgbox.setAlignment(QtCore.Qt.AlignRight)
        self.plt_frame = PlotCanvas(self)      
        imgbox.addWidget(self.plt_frame)

        # create the labels beneath image. Numeric controls are added in the
        # respective subfunctions.
        lbox_img = QtWidgets.QVBoxLayout()
        lbox_img.addWidget(QtWidgets.QLabel('Offset X')) 
        lbox_img.addWidget(QtWidgets.QLabel('Offset Y'))
        lbox_img.addWidget(QtWidgets.QLabel('Grating X'))
        lbox_img.addWidget(QtWidgets.QLabel('Grating Y'))
        lbox_img.addWidget(QtWidgets.QLabel('Defocus L/R'))
        lbox_img.addWidget(QtWidgets.QLabel('STED Mode'))
        lbox_img.addWidget(QtWidgets.QLabel('Radius'))
        lbox_img.addWidget(QtWidgets.QLabel('Phase'))
        lbox_img.addWidget(QtWidgets.QLabel('Rotation'))
        lbox_img.addWidget(QtWidgets.QLabel('Steps'))
        lbox_img.addWidget(QtWidgets.QLabel('Astigmatism X/Y'))
        lbox_img.addWidget(QtWidgets.QLabel('Coma X/Y'))
        lbox_img.addWidget(QtWidgets.QLabel('Spherical 1/2'))
        lbox_img.addWidget(QtWidgets.QLabel('Trefoil Vert/Obl'))
        
        lbox_img.setContentsMargins(0,0,0,0)
        
        # create the controls provided by img_l, img_r and img_aberr
        # these are used to set the parameters to create the patterns
        c = QtWidgets.QGridLayout()
        c.addLayout(lbox_img, 0, 0, 1, 1)
        if self.p.general["split_image"]:
            c.addLayout(self.img_l.create_gui(self.p, self.p.left), 0, 1, 1, 2)
            c.addLayout(self.img_r.create_gui(self.p, self.p.right), 0, 3, 1, 2)
        else:
            c.addLayout(self.img_full.create_gui(self.p, self.p.full), 0, 1, 1, 2)
            
        c.setAlignment(QtCore.Qt.AlignRight)
        c.setContentsMargins(0,0,0,0)
        
        # add all the widgets
        vbox.addLayout(imgbox)
        vbox.addLayout(c)
        vbox.setContentsMargins(0,0,0,0)
        self.main_frame.setLayout(vbox)       
        self.setCentralWidget(self.main_frame)
           
    
    def correct_tiptiltdefoc(self, img):
        c = self.p.simulation["optical_params_sted"]
        mag = self.p.general["slm_mag"]
        size = 2 * np.asarray(self.p.general["size_slm"])   
        off = [self.img_l.off.xgui.value(), self.img_l.off.ygui.value()]
        
        d_xyz = helpers.get_CoMs(img) # in px!
        
        h = (np.pi * c["px_size"]) / (c["lambda"] * c["f"]) * c["obj_ba"] / mag / 2
        xtilt =  h * d_xyz[0]
        ytilt = -h * d_xyz[1]
        #calculate tip/tilt as zernike polynomials [1,1] and [-1,1]
        full = pcalc.zern_sum(size, [xtilt, ytilt], [[1,-1],[1,1]], radscale = 2)
        tiptilt_correct = pcalc.crop(full, size//2, offset = off)
        self.phase_tiptilt = self.phase_tiptilt + tiptilt_correct  
        
        h = c["px_size"]/((c["f"]/c["obj_ba"])**2 *8 * np.sqrt(3)*c["lambda"])/2 
        defocus = h * d_xyz[2]
        # calculate defocus as zernike polynomial [2,0]
        full = pcalc.zern_sum(size, [defocus], [[2,0]], radscale = 2)
        defoc_correct = pcalc.crop(full, size//2, offset = off)
        self.phase_defocus = self.phase_defocus + defoc_correct
        self.recalc_images()
      
    
    def corrective_loop(self, scope, image=None, aberrs = np.zeros(11), offset=False, multi=False, ortho_sec=False, i=1):
        """ Passes trained model and acquired image to abberior_predict to 
            estimate zernike weights and offsets required to correct 
            aberrations. Calculates new SLM pattern to acquire new image and 
            calculates correlation coefficients. """
        
        size = 2 * np.asarray(self.p.general["size_slm"])
        scale = 2 * pcalc.get_mm2px(self.p.general["slm_px"], self.p.general["slm_mag"])
        orders = self.p.simulation["numerical_params"]["orders"]
        print("model into predict: ", self.p.general["autodl_model_path"])
        delta_zern, delta_off = microscope.abberior_predict(self.p.general["autodl_model_path"], 
                                                            self.p.model_def,
                                                            image, ii=i)
        delta_off = delta_off * scale
        #if abs(delta_off[0]) > 32:
        #    delta_off = 0
        #elif abs(delta_off[1]) > 32:
        #    delta_off = 0

        off = [self.img_l.off.xgui.value() + delta_off[1],
               self.img_l.off.ygui.value() - delta_off[0]]
        self.img_l.off.xgui.setValue(np.round(off[0]))
        self.img_l.off.ygui.setValue(np.round(off[1]))
        
        print("TODO: stats for debugging. ", aberrs, delta_zern, size, 
              self.slm_radius, scale, delta_off, off)
        new_aberrs = aberrs - delta_zern
        full = pcalc.zern_sum(size, new_aberrs, orders[3::], np.sqrt(2)*self.slm_radius)
        self.phase_zern = pcalc.crop(full, size//2, offset = off)
        self.recalc_images()
        img, stats = scope.acquire_image(multi=multi, mask_offset = off, aberrs = new_aberrs)
        self.correct_tiptiltdefoc(img)
            
        new_img, stats = scope.acquire_image(multi=multi, mask_offset = off, aberrs = new_aberrs)
        correlation = np.round(helpers.corr_coeff(new_img, multi=multi), 2)                                                                                                                                                                                                                                                                                                                                                                                                                                                                   
        print('correlation coeff is: {}'.format(correlation))
        
        return delta_zern, delta_off, new_img, correlation

    def auto_align(self, so_far = -1, best_of = 5, multi = True, offset = True):
        """This function calls abberior from AutoAlign module, passes the resulting dictionary
        through a constructor for a param object
        so_far: correlation required to stop optimizing; -1 means it only executes once"""

        size = 2 * np.asarray(self.p.general["size_slm"])
        orders = self.p.simulation["numerical_params"]["orders"]
        scope = microscope.Microscope(self.p.simulation)
        #imspector, msr_names, active_msr, conf = scope.get_config()
        # center the image before starting
        self.correct_tiptilt(scope)
        if multi:
            self.correct_defocus(scope)
            
        corr = 0
        i = 0
        new_aberrs = np.zeros(11)
        old_aberrs = new_aberrs
        while corr >= so_far:
            image = scope.acquire_image(multi=multi, mask_offset = [0,0], aberrs = new_aberrs)[0]                                              
            delta_zern, delta_off, image, new_corr = self.corrective_loop(scope, image, aberrs = new_aberrs, 
                                                   offset=offset, multi=multi, i=best_of)
            if new_corr > corr:
                print('iteration: ', i, 'new corr: {}, old corr: {}'.format(new_corr, corr))
                corr = new_corr
                old_aberrs = new_aberrs
                new_aberrs = old_aberrs - delta_zern
                i = i + 1
            else:
                print('final correlation: {}'.format(corr))
                # REMOVING the last phase corrections from the SLM
                off = [self.img_l.off.xgui.value() - delta_off[1],
                       self.img_l.off.ygui.value() + delta_off[0]]
                full = pcalc.zern_sum(size, old_aberrs, orders[3::], np.sqrt(2)*self.slm_radius)
                self.phase_zern = pcalc.crop(full, size//2, offset = off)
                i -= 1
                break
        self.recalc_images()


    def automate(self):

        num_its=2
        px_size = 10*1e-9
        i_start = 0
        best_of = 5
        size = 2 * np.asarray(self.p.general["size_slm"])
        orders = self.p.simulation["numerical_params"]["orders"]
        scale = 2 * pcalc.get_mm2px(self.p.general["slm_px"], self.p.general["slm_mag"])
        
        #TODO: implement offsets here!
        plane = [0,0,0]
        
        # 0. creates data structure for statistics
        statistics = {'gt_off': [], 'preds_off': [], 
                      'gt_zern': [], 'preds_zern': [], 
                      'init_corr': [],'corr': []}
        # for model name: drop everything from model path, drop extension
        mdl_name = self.p.general["autodl_model_path"].split("/")[-1][:-4]
        path = self.p.general["data_path"] + mdl_name
        self.p.load_model_def('', 'model_params.json', mdl_name)
        
        # TODO: clean up usage of these flags later
        multi = self.p.model_def['multi_flag']
        ortho_sec = self.p.model_def['orthosec_flag']
        offset = self.p.model_def['offset_flag']
        zern_flag = self.p.model_def['zern_flag']
        
        print("save path: ", path, "\n used model: ", mdl_name)
        try:
            if not os.path.isdir(self.p.general["data_path"]):
                os.mkdir(self.p.general["data_path"])
            if not os.path.isdir(path):
                os.mkdir(path)
        except:
            print("couldn't create directory!")
        
        scope = microscope.Microscope(self.p.simulation)
        if self.groundtruth == None:
            virtual_scope = microscope.Microscope(self.p.simulation)
            #self.groundtruth = virtual_scope.calc_groundtruth(1.1)
        
        #imspector, msr_names, active_msr, conf = scope.get_config()
        xyz_init = scope.get_stage_offsets()
        for ii in range(num_its):
            # 1. zeroes SLM
            self.reload_params(self.param_path)
            # 2. get image from microscope and center
            img, stats = scope.acquire_image(multi=ortho_sec, mask_offset = [0,0], aberrs = np.zeros(11))        
            scope.center_stage(img, xyz_init, px_size, mode = 'fine')
            
            # 3. dials in random aberrations and sends them to SLM and SLM GUI
            #TODO: don't hardcode this anymore depending on model used
            #WHOLE BLOCK; BOTH for aberrs as well as off_aberr
            if zern_flag:
                aberrs = helpers.gen_coeffs(11)
            else:
                aberrs = [0 for c in range(11)]
            if offset:
                ba = self.p.objectives[self.current_objective]["backaperture"]
                off_aberr = [np.round(scale*x) for x in helpers.gen_offset(ba, 0.1)]
            else:
                off_aberr = [0,0]
            
            # calculate new offsets and write to GUI, recalc SLM image
            off = [self.img_l.off.xgui.value() - off_aberr[1],
                   self.img_l.off.ygui.value() + off_aberr[0]]
            self.img_l.off.xgui.setValue(off[0])
            self.img_l.off.ygui.setValue(off[1])
            #TODO: sanity check that offsets are within boundaries
            full = pcalc.zern_sum(size, aberrs, orders[3::], np.sqrt(2)*self.slm_radius)
            self.phase_zern = pcalc.crop(full, size//2, offset = off)
            
            self.recalc_images()
            
            # 4. Acquire image, center once more using tip tilt and defocus corrections
            # save image and write correction coefficients to file
            img, stats = scope.acquire_image(multi=ortho_sec, mask_offset = off_aberr, aberrs = aberrs)
            self.correct_tiptiltdefoc(img)
                
            #TODO: change scope.acquire_image to return always array, then always use img[0]
            img_aberr, stats = scope.acquire_image(multi=multi, mask_offset = off_aberr, aberrs = aberrs)
            statistics['gt_zern'].append(aberrs)
            statistics['gt_off'].append(off_aberr)
            statistics['init_corr'].append(helpers.corr_coeff(img_aberr, multi=multi))
            scope.save_img(path + '/' + str(ii+i_start) + "_aberrated")
            
            # 5. single pass correction
            delta_zern, delta_off, img_corr, corr = self.corrective_loop(scope, img_aberr, aberrs, offset=offset, multi=multi, i = best_of)
            
            statistics['preds_zern'].append(delta_zern.tolist())
            statistics['preds_off'].append(delta_off.tolist())
            statistics['corr'].append(corr)
            scope.save_img(path + '/' + str(ii+i_start) + "_corrected")
            with open(path + '/' + mdl_name +str(i_start)+'.txt', 'w') as file:
                json.dump(statistics, file)

            # use matplotlib to plot and save data
            fig = plt.figure()
            minmax = [np.min(img_corr[0]), np.max(img_corr[0])]
            if ortho_sec and multi:
                plt.subplot(231); plt.axis('off')
                plt.imshow(img_aberr[0], clim = minmax, cmap = 'inferno')
                plt.subplot(232); plt.axis('off')
                plt.imshow(img_aberr[1], clim = minmax, cmap = 'inferno')
                plt.subplot(233); plt.axis('off')
                plt.imshow(img_aberr[2], clim = minmax, cmap = 'inferno')
                plt.subplot(234); plt.axis('off')
                plt.imshow(img_corr[0], clim = minmax, cmap = 'inferno')
                plt.subplot(235); plt.axis('off')
                plt.imshow(img_corr[1], clim = minmax, cmap = 'inferno')
                plt.subplot(236); plt.axis('off')
                plt.imshow(img_corr[2], clim = minmax, cmap = 'inferno')
            elif ortho_sec and not multi:
                plt.subplot(121); plt.axis('off')
                plt.imshow(img_aberr, clim = minmax, cmap = 'inferno')
                plt.subplot(122); plt.axis('off')
                plt.imshow(img_corr[0], clim = minmax, cmap = 'inferno')
            fig.savefig(path + '/' + str(ii+i_start) + "_thumbnail.png")
            #TODO add missing logic blocks

        print('DONE with automated loop!', '\n', 
              'Initial correlation: ', statistics['init_corr'], '\n', 
              'final correlation: ', statistics['corr'])


    def crea_but(self, box, action, name, param = None):
        """ Creates and labels a button and connects button and action. Input: 
            Qt layout to place the button, function: action to perform, string: 
            name of the button. Returns the button. """
        button = QtWidgets.QPushButton(name, self)
        if param == None:
            button.clicked.connect(action)
        else:
            button.clicked.connect(lambda: action(param))
        button.setMaximumSize(120,50)
        box.addWidget(button)
        box.setAlignment(button, QtCore.Qt.AlignVCenter)
        box.setContentsMargins(0,0,0,0)

        return button


    def labeled_qt(self, QtItem, label, main_layout):
        """ Adds a QtItem with a label. Inputs: function pointer to the QtItem,
            label string, main_layout to put the item in. """
        box = QtWidgets.QHBoxLayout()     
        box.addWidget(QtWidgets.QLabel(label))
        item = QtItem()
        box.addWidget(item)
        main_layout.addLayout(box)
        return item


    def crea_checkbox(self, box, action, name, state, param = None):
        """ Creates and labels a checkbox and connects button and action. Input: 
            Qt layout to place the button, function: action to perform, string: 
            name of the button. Returns the button. """
        checkbox = QtWidgets.QCheckBox(name, self)
        if param == None:
            checkbox.clicked.connect(action)
        else:
            checkbox.clicked.connect(lambda: action(param))
        box.addWidget(checkbox)
        box.setAlignment(checkbox, QtCore.Qt.AlignVCenter)   
        box.setContentsMargins(0,0,0,0)
        if state:
            checkbox.setChecked(True)
        return checkbox     


    def openFileDialog(self, path):
        """ Creates a dialog to open a file. At the moement, it is only used 
            to load the image for the flat field correction. There is no 
            sanity check implemented whether the selected file is a valid image. """
        options = QtWidgets.QFileDialog.Options()
        options |= QtWidgets.QFileDialog.DontUseNativeDialog
        work_dir = os.path.dirname(os.path.realpath(__file__))
        fileName, _ = QtWidgets.QFileDialog.getOpenFileName(self, 
                        "Load flat field correction", work_dir +'/'+ path)
        if fileName:
            return fileName
        else:
            return None

        
    def openFlatFieldDialog(self, path):
        """ Creates a dialog to open a file. At the moement, it is only used 
            to load the image for the flat field correction. There is no 
            sanity check implemented whether the selected file is a valid image. """
        options = QtWidgets.QFileDialog.Options()
        options |= QtWidgets.QFileDialog.DontUseNativeDialog
        work_dir = os.path.dirname(os.path.realpath(__file__))
        fileName, _ = QtWidgets.QFileDialog.getOpenFileName(self, 
                        "Load flat field correction", work_dir +'/'+ path)
        # TODO: needs to be implemented to work for two paths, one for left, 
        # one for right side
        print("Currently not implemented. Please add paths in the files for \
              left and right side parameters as 'cal1'.")
        #if fileName:
        #    self.load_flat_field(fileName)
        #    self.combine_and_update()

    
    def load_flat_field(self, path_l, path_r, recalc = True):
        """ Opens the images in the parameter paths, combines two halves to 
            one image and sets as new flatfield correction. """
     
        s = np.asarray(self.p.general["size_slm"])    
        lhalf = pcalc.crop(np.asarray(pcalc.load_image(path_l))/255, 
                           s, [ s[1] // 2, s[0] // 2])
        rhalf = pcalc.crop(np.asarray(pcalc.load_image(path_r))/255, 
                           s, [-(s[1] // 2), s[0] // 2])
        
        # check whethere double pass is activated and cross correction as on 
        # Abberior should be applied: det offsets to [0,0] for not activated        
        if self.p.general["double_pass"]:            
            ff_l_patched = np.zeros([2 * s[0], 2 * s[1]])
            ff_l_patched[s[0] // 2 : 3 * s[0] // 2, s[1] // 2 : 3 * s[1] // 2] = lhalf
            ff_r_patched = np.zeros([2 * s[0], 2 * s[1]])
            ff_r_patched[s[0] // 2 : 3 * s[0] // 2, s[1] // 2 : 3 * s[1] // 2] = rhalf
            off = self.img_r.offset - self.img_l.offset
            lhalf = lhalf + pcalc.crop(ff_r_patched, s, -off)
            rhalf = rhalf + pcalc.crop(ff_l_patched, s,  off)

        self.flatfieldcor = [lhalf, rhalf]
        self.flat_field(self.p.general["flat_field"], recalc)


    def flat_field(self, state, recalc = True):
        """ Opens the image in the parameter path and sets as new flatfield
            correction. """
        self.p.general["flat_field"] = int(state)
        if state:
            self.flatfield = self.flatfieldcor
        else:
            self.flatfield = [np.zeros_like(self.flatfieldcor[0]), 
                              np.zeros_like(self.flatfieldcor[1])]
        if recalc:
            self.combine_and_update()
        
        
    def single_correction(self, state):
        """ Action called when the "Single Correction" checkbox is selected.
            Toggles between identical correction for both halves of the sensor
            and using individidual corrections. When single correction is 
            active, the values from the left sensor half are used. """
        self.p.general["single_aberr"] = int(state)
        if state:
            self.img_r.aberr.astig.xgui.setValue(self.img_l.aberr.astig.xgui.value())
            self.img_r.aberr.astig.ygui.setValue(self.img_l.aberr.astig.ygui.value())
            self.img_r.aberr.coma.xgui.setValue(self.img_l.aberr.coma.xgui.value())
            self.img_r.aberr.coma.ygui.setValue(self.img_l.aberr.coma.ygui.value())
            self.img_r.aberr.sphere.xgui.setValue(self.img_l.aberr.sphere.xgui.value())
            self.img_r.aberr.sphere.ygui.setValue(self.img_l.aberr.sphere.ygui.value())
            self.img_r.aberr.trefoil.xgui.setValue(self.img_l.aberr.trefoil.xgui.value())
            self.img_r.aberr.trefoil.ygui.setValue(self.img_l.aberr.trefoil.ygui.value())
            
    def double_pass(self, state):
        """ Activates the double pass geometry cross correction as on Abberior.
            The laser beam hits the SLM twice, once it's modulated, once not 
            (because it has the wrong polarization). To still correct for SLM
            curvature during the unmodulated reflection, the flatfield pattern
            from the first impact needs to be shifted by the offset and added 
            to the flatfield correction of the second impact. """
        self.p.general["double_pass"] = int(state)
        if self.p.general["flat_field"]:#flt_fld_state.checkState():
            print("calling flatfield")
            if self.p.general["split_image"]:
                self.load_flat_field(self.p.left["cal1"], self.p.right["cal1"])
            else:
                self.load_flat_field(self.p.full["cal1"], self.p.full["cal1"])
        
    
    def calc_slmradius(self, backaperture, mag):
        """ Calculates correct scaling factor for SLM based on objective
            backaperture, optical magnification of the beampath, SLM pixel
            size and size of the SLM. Required values are directly taken from
            the parameters files. """
            
        rad = pcalc.normalize_radius(backaperture, mag, 
                    self.p.general["slm_px"], self.p.general["size_slm"])
        return rad
    
    
    def radius_changed(self):
        """ Radius of pattern on SLM can be hardcoded instead of calculating
            from the objectives backaperture and optical magnification. """
        self.radius_input = self.rad_but.value()
        print("radius changed")
        self.slm_radius = self.calc_slmradius(self.radius_input, 1)
        self.init_zernikes()
        self.recalc_images()

            
    def objective_changed(self):
        """ Action called when the users selects a different objective. 
            Calculates the diameter of the BFP; then recalculates the the
            patterns based on the selected objective. """
        self.current_objective = self.obj_sel.currentText()#["name"]
        self.reload_params(self.param_path)
        self.slm_radius = self.calc_slmradius(
            self.p.objectives[self.current_objective]["backaperture"],
            self.p.general["slm_mag"])
        self.init_zernikes()
        self.recalc_images()
    
        
    def recalc_images(self):
        """ Function to recalculate the left and right images completely. 
            Update is set to false to prevent redrawing after every step of 
            the recalculation. Image display is only updated once at the end. """
        if self.p.general["split_image"]:
            self.img_l.update(update = False, completely = True)
            self.img_r.update(update = False, completely = True)
        else:
            self.img_full.update(update = False, completely = True)
        self.combine_and_update()

        
    def combine_and_update(self):
        """ Stitches the images for left and right side of the SLM, adds the 
            flatfield correction and phasewraps everything. Updates
            self.img_data with the new image data.
            Updates the displayed images in the control window and (if active)
            on the SLM. To do so, rescales the image date to the SLM's required
            pitch (depends on the wavelength, and is set in the general 
            parameters). Saves the image patterns/latest.bmp and then reloads
            into the Pixmap for display. """
        
        if self.p.general["split_image"]:
            l = pcalc.phase_wrap(pcalc.add_images([self.img_l.data, 
                                                   self.flatfield[0],
                                                   self.phase_zern, 
                                                   self.phase_tiptilt,
                                                   self.phase_defocus]), 
                                 self.p.left["phasewrap"])
            r = pcalc.phase_wrap(pcalc.add_images([self.img_r.data,
                                                   self.flatfield[1],
                                                   self.phase_zern, 
                                                   self.phase_tiptilt,
                                                   self.phase_defocus]), 
                                 self.p.right["phasewrap"])
            # this is hack:
            # for preview display, do not scale with SLM range
            # for SLM display, do scale ... scales may differ left / right
            self.img_data = pcalc.stitch_images(l * self.p.left["slm_range"],
                                                r * self.p.right["slm_range"])
            
            
            self.plt_frame.plot(pcalc.stitch_images(l, r))
                                
        else:            
            self.img_data = pcalc.phase_wrap(pcalc.add_images([self.img_full.data, 
                                                               pcalc.stitch_images(self.flatfield[0], self.flatfield[1]), 
                                                               self.phase_zern, 
                                                               self.phase_tiptilt,
                                                               self.phase_defocus]), 
                                             self.p.general["phasewrap"])
            self.plt_frame.plot(self.img_data)
            self.img_data = self.img_data * self.p.general["slm_range"]
            
        if self.slm != None:
            self.slm.update_image(np.uint8(self.img_data))

 
    def open_SLMDisplay(self):
        """ Opens a widget fullscreen on the secondary screen that displays
            the latest image. """
        self.slm = SLM.SLM_Display(np.uint8(self.img_data), self.p.general["display_mode"])

        
    def close_SLMDisplay(self):
        """ Closes the SLM window (if it exists) and resets reference to None 
            to prevent errors when trying to close SLM again. """
        if self.slm != None:
            self.slm._quit()
            self.slm = None


    def _quit(self):
        """ Action called when the Quit button is pressed. Closes the SLM 
            control window and exits the main window. """
        pcalc.save_image(self.img_data, self.p.general["path"], 
                         self.p.general["last_img_nm"])
        self.close_SLMDisplay()           
        self.close()


class App(QtWidgets.QApplication):
    """ Creates the App containing all the widgets. Event handling to exit
        properly once the last window is closed, even when the Quit button
        was not used. """
    def __init__(self, *args):
        QtWidgets.QApplication.__init__(self, *args)
        self.main = Main_Window(self)
        self.lastWindowClosed.connect(self.byebye)
        self.main.show()


    def byebye( self ):
        print("byebye")
        self.exit(0)
    
    
def main(args):
    global app
    app = App(args)
    app.exec_()


if __name__ == "__main__":
    """ makes sure that main is only executed if this code is the main program.
        The classes defined here are accessible from the outside as well, but
        then main isn't executed. """
    main(sys.argv)
