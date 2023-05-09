import os.path
from importlib import reload
from pprint import pprint
import gradio as gr
import modules.ui
from modules import paths, scripts, shared
from modules.processing import Processed
from modules.script_callbacks import (CFGDenoisedParams, CFGDenoiserParams, on_cfg_denoised, on_cfg_denoiser)
import scripts.attention
import scripts.latent
import scripts.regions
reload(scripts.regions) # update without restarting web-ui.bat
reload(scripts.attention)
reload(scripts.latent)
import json  # Presets.
from json.decoder import JSONDecodeError
from scripts.attention import (TOKENS, hook_forwards, reset_pmasks, savepmasks)
from scripts.latent import (denoised_callback_s, denoiser_callback_s, lora_namer, restoremodel, setloradevice, setuploras, unloadlorafowards)
from scripts.regions import (CBLACK, IDIM, KEYBRK, KEYBASE, KEYCOMM, KEYPROMPT, create_canvas, detect_mask, detect_polygons, floatdef, inpaintmaskdealer, makeimgtmp, matrixdealer)

def lange(l):
    return range(len(l))

orig_batch_cond_uncond = shared.batch_cond_uncond

PRESETS =[
    ["Vertical-3", "Vertical",'1,1,1',"",False,False,False,"Attention",False,"0","0"],
    ["Horizontal-3", "Horizontal",'1,1,1',"",False,False,False,"Attention",False,"0","0"],
    ["Horizontal-7", "Horizontal",'1,1,1,1,1,1,1',"0.2",True,False,False,"Attention",False,"0","0"],
    ["Twod-2-1", "Horizontal",'1,2,3;1,1',"0.2",False,False,False,"Attention",False,"0","0"],
]

ATTNSCALE = 8 # Initial image compression in attention layers.

class Script(modules.scripts.Script):
    def __init__(self):
        self.active = False
        self.mode = ""
        self.calcmode = ""
        self.w = 0
        self.h = 0
        self.debug = False
        self.usebase = False
        self.usecom = False
        self.usencom = False
        self.batch_size = 0

        self.cells = False
        self.aratios = []
        self.bratios = []
        self.divide = 0
        self.count = 0
        self.pn = True
        self.hr = False
        self.hr_scale = 0
        self.hr_w = 0
        self.hr_h = 0
        self.all_prompts = []
        self.all_negative_prompts = []
        self.imgcount = 0
        # for latent mode
        self.filters = []
        self.neg_filters = []
        self.anded = False
        self.lora_applied = False
        self.lactive = False
        # for inpaintmask
        self.indmaskmode = False
        self.regmasks = None
        self.regbase = None
        #for prompt region
        self.pe = []
        self.modep =False
        self.calced = False
        self.step = 0
        self.lpactive = False

    def title(self):
        return "Regional Prompter"

    def show(self, is_img2img):
        return modules.scripts.AlwaysVisible

    infotext_fields = None
    paste_field_names = []

    def ui(self, is_img2img):
        path_root = modules.scripts.basedir()
        filepath = os.path.join(path_root,"scripts", "regional_prompter_presets.json")

        presets = []

        presets = loadpresets(filepath)

        with gr.Accordion("Regional Prompter", open=False):
            with gr.Row():
                active = gr.Checkbox(value=False, label="Active",interactive=True,elem_id="RP_active")
            with gr.Row():
                mode = gr.Radio(label="Divide mode", choices=["Horizontal", "Vertical","Mask","Prompt","Prompt-Ex"], value="Horizontal",  type="value", interactive=True)
                calcmode = gr.Radio(label="Generation mode", choices=["Attention", "Latent"], value="Attention",  type="value", interactive=True)
            with gr.Row(visible=True):
                ratios = gr.Textbox(label="Divide Ratio",lines=1,value="1,1",interactive=True,elem_id="RP_divide_ratio",visible=True)
                baseratios = gr.Textbox(label="Base Ratio", lines=1,value="0.2",interactive=True,  elem_id="RP_base_ratio", visible=True)
            with gr.Row():
                usebase = gr.Checkbox(value=False, label="Use base prompt",interactive=True, elem_id="RP_usebase")
                usecom = gr.Checkbox(value=False, label="Use common prompt",interactive=True,elem_id="RP_usecommon")
                usencom = gr.Checkbox(value=False, label="Use common negative prompt",interactive=True,elem_id="RP_usecommon")
            with gr.Row():
                with gr.Column():
                    maketemp = gr.Button(value="visualize and make template")
                    template = gr.Textbox(label="template",interactive=True,visible=True)
                with gr.Column():
                    areasimg = gr.Image(type="pil", show_label  = False).style(height=256,width=256)
                    threshold = gr.Textbox(label = "threshold",value = 0.4,interactive=True,)

            with gr.Row():
                polymask = gr.Image(label = "Mask mode",elem_id="polymask",
                                        source = "upload", mirror_webcam = False, type = "numpy", tool = "sketch")
            with gr.Row():
                with gr.Column():
                    num = gr.Slider(label="Region", minimum=0, maximum=CBLACK, step=1, value=1)
                    canvas_width = gr.Slider(label="Canvas Width", minimum=64, maximum=2048, value=512, step=8)
                    canvas_height = gr.Slider(label="Canvas Height", minimum=64, maximum=2048, value=512, step=8)
                    btn = gr.Button(value = "Draw region")
                    btn2 = gr.Button(value = "Display mask")
                    cbtn = gr.Button(value="Create mask area")
                with gr.Column():
                    showmask = gr.Image(shape=(IDIM, IDIM))
            btn.click(detect_polygons, inputs = [polymask,num], outputs = [polymask,num])
            btn2.click(detect_mask, inputs = [polymask,num], outputs = [showmask])
            cbtn.click(fn=create_canvas, inputs=[canvas_height, canvas_width], outputs=[polymask])

            with gr.Accordion("Presets",open = False):
                with gr.Row():
                    availablepresets = gr.Dropdown(label="Presets", choices=[pr["name"] for pr in presets], type="index")
                    applypresets = gr.Button(value="Apply Presets",variant='primary',elem_id="RP_applysetting")
                with gr.Row():
                    presetname = gr.Textbox(label="Preset Name",lines=1,value="",interactive=True,elem_id="RP_preset_name",visible=True)
                    savesets = gr.Button(value="Save to Presets",variant='primary',elem_id="RP_savesetting")
            with gr.Row():
                nchangeand = gr.Checkbox(value=False, label="disable convert 'AND' to 'BREAK'", interactive=True, elem_id="RP_ncand")
                debug = gr.Checkbox(value=False, label="debug", interactive=True, elem_id="RP_debug")
                lnter = gr.Textbox(label="LoRA in negative textencoder",value="0",interactive=True,elem_id="RP_ne_tenc_ratio",visible=True)
                lnur = gr.Textbox(label="LoRA in negative U-net",value="0",interactive=True,elem_id="RP_ne_unet_ratio",visible=True)
            settings = [mode, ratios, baseratios, usebase, usecom, usencom, calcmode, nchangeand, lnter, lnur, threshold]
        
        self.infotext_fields = [
                (active, "RP Active"),
                (mode, "RP Divide mode"),
                (calcmode, "RP Calc Mode"),
                (ratios, "RP Ratios"),
                (baseratios, "RP Base Ratios"),
                (usebase, "RP Use Base"),
                (usecom, "RP Use Common"),
                (usencom, "RP Use Ncommon"),
                (nchangeand,"RP Change AND"),
                (lnter,"RP LoRA Neg Te Ratios"),
                (lnur,"RP LoRA Neg U Ratios"),
                (threshold,"RP threshold"),
        ]

        for _,name in self.infotext_fields:
            self.paste_field_names.append(name)

        def setpreset(select):
            presets = loadpresets(filepath)
            preset = presets[select]
            preset = [fmt(preset.get(k, vdef)) for (k,fmt,vdef) in PRESET_KEYS]
            preset = preset[1:] # Remove name.
            # TODO: Need to grab current value from gradio. Must we send it as input?
            preset = ["" if p is None else p for p in preset]
            return [gr.update(value = pr) for pr in preset]
        

        maketemp.click(fn=makeimgtmp, inputs =[ratios,mode,usecom,usebase],outputs = [areasimg,template])
        applypresets.click(fn=setpreset, inputs = availablepresets, outputs=settings)
        savesets.click(fn=savepresets, inputs = [presetname,*settings],outputs=availablepresets)
                
        return [active, debug, mode, ratios, baseratios, usebase, usecom, usencom, calcmode, nchangeand, lnter, lnur, threshold, polymask]

    def process(self, p, active, debug, mode, aratios, bratios, usebase, usecom, usencom, calcmode, nchangeand, lnter, lnur, threshold, polymask):
        if not active:
            unloader(self,p)
            return p

        p.extra_generation_params.update({
            "RP Active":active,
            "RP Divide mode":mode,
            "RP Calc Mode":calcmode,
            "RP Ratios": aratios,
            "RP Base Ratios": bratios,
            "RP Use Base":usebase,
            "RP Use Common":usecom,
            "RP Use Ncommon": usencom,
            "RP Change AND" : nchangeand,
            "RP LoRA Neg Te Ratios": lnter,
            "RP LoRA Neg U Ratios": lnur,
            "RP threshold": threshold,
                })

        savepresets("lastrun",mode, aratios,bratios, usebase, usecom, usencom, calcmode, nchangeand, lnter, lnur, threshold, polymask)

        self.__init__()

        self.active = True
        self.calcmode = calcmode
        self.debug = debug
        self.usebase = usebase
        self.usecom = usecom
        self.usencom = usencom
        self.w = p.width
        self.h = p.height
        self.batch_size = p.batch_size
        self.prompt = p.prompt
        self.all_prompts = p.all_prompts.copy()
        self.all_negative_prompts = p.all_negative_prompts.copy()

        comprompt = comnegprompt = None

        # SBM ddim / plms detection.
        self.isvanilla = p.sampler_name in ["DDIM", "PLMS", "UniPC"]

        if self.h % ATTNSCALE != 0 or self.w % ATTNSCALE != 0:
            # Testing shows a round down occurs in model.
            print("Warning: Nonstandard height / width.")
            self.h = self.h - self.h % ATTNSCALE
            self.w = self.w - self.w % ATTNSCALE

        if hasattr(p,"enable_hr"): # Img2img doesn't have it.
            self.hr = p.enable_hr
            self.hr_w = (p.hr_resize_x if p.hr_resize_x > p.width else p.width * p.hr_scale)
            self.hr_h = (p.hr_resize_y if p.hr_resize_y > p.height else p.height * p.hr_scale)
            if self.hr_h % ATTNSCALE != 0 or self.hr_w % ATTNSCALE != 0:
                # Testing shows a round down occurs in model.
                print("Warning: Nonstandard height / width for ulscaled size")
                self.hr_h = self.hr_h - self.hr_h % ATTNSCALE
                self.hr_w = self.hr_w - self.hr_w % ATTNSCALE

        self.mode = mode

        self, p = flagfromkeys(self, p)

        self.indmaskmode = (mode == "Mask")

        if not nchangeand and "AND" in p.prompt.upper():
            p.prompt = p.prompt.replace("AND",KEYBRK)
            for i in lange(p.all_prompts):
                p.all_prompts[i] = p.all_prompts[i].replace("AND",KEYBRK)
            self.anded = True
            

        if "Prompt" not in mode: # skip region assign in prompt mode
            self.cells = not "Mask" in mode

            #convert BREAK to ADDCOL/ADDROW
            if KEYBRK in p.prompt and not "Mask" in mode:
                p = keyconverter(aratios, mode, usecom, usebase, p)

            ##### region mode

            if self.indmaskmode:
                self, p = inpaintmaskdealer(self, p, bratios, usebase, polymask, comprompt, comnegprompt)

            elif self.cells:
                self, p = matrixdealer(self, p, aratios, bratios, mode, usebase, comprompt,comnegprompt)
    
            ##### calcmode 

            if calcmode == "Attention":
                self.handle = hook_forwards(self, p.sd_model.model.diffusion_model)
                shared.batch_cond_uncond = orig_batch_cond_uncond
                seps = KEYBRK 
            else:
                self.handle = hook_forwards(self, p.sd_model.model.diffusion_model,remove = True)
                setuploras(self,p)
                if self.debug : print(p.prompt)
                seps = "AND"

            # seps = KEYBRK # SBM No longer is keybrk applied first.

        elif "Prompt" in mode: #Prompt mode use both calcmode
            if not (KEYBRK in p.prompt.upper() or "AND" in p.prompt.upper() or KEYPROMPT in p.prompt.upper()):
                self.active = False
                unloader(self,p)
                return
            self.ex = "Ex" in mode
            self.modep = True
            if not usebase : bratios = "0"
            self.handle = hook_forwards(self, p.sd_model.model.diffusion_model)
            denoiserdealer(self)

            if calcmode == "Latent":
                seps = "AND"
                self.lpactive = True
            else:
                seps = KEYBRK

        self, p = commondealer(self, p, self.usecom, self.usencom)   #add commom prompt to all region
        self, p = anddealer(self, p , calcmode)                                 #replace BREAK to AND
        self = tokendealer(self, p, seps)                             #count tokens and calcrate target tokens
        self, p = thresholddealer(self, p, threshold)                          #set threshold
        self = bratioprompt(self, bratios)
                  

        print(f"pos tokens : {self.ppt}, neg tokens : {self.pnt}")
        if debug : debugall(self)

    def before_process_batch(self, p, active, debug, mode, aratios, bratios, usebase, usecom, usencom, calcmode,nchangeand, lnter, lnur, threshold, polymask,**kwargs):
        self.current_prompts = kwargs["prompts"].copy()

    def process_batch(self, p, active, debug, mode, aratios, bratios, usebase, usecom, usencom, calcmode,nchangeand, lnter, lnur, threshold, polymask,**kwargs):
        # print(kwargs["prompts"])
        if active:
            p.all_prompts[p.iteration * p.batch_size:(p.iteration + 1) * p.batch_size] = self.all_prompts[p.iteration * p.batch_size:(p.iteration + 1) * p.batch_size]
            p.all_negative_prompts[p.iteration * p.batch_size:(p.iteration + 1) * p.batch_size] = self.all_negative_prompts[p.iteration * p.batch_size:(p.iteration + 1) * p.batch_size]

            if self.modep:
                self = reset_pmasks(self)
            if calcmode =="Latent":
                setloradevice(self) #change lora device cup to gup and restore model in new web-ui lora method
                lora_namer(self, p, lnter, lnur)

                if self.lora_applied: # SBM Don't override orig twice on batch calls.
                    pass
                else:
                    restoremodel(p)
                    denoiserdealer(self)
                    self.lora_applied = True

    # TODO: Should remove usebase, usecom, usencom - grabbed from self value.
    def postprocess_image(self, p, pp, active, debug, mode, aratios, bratios, usebase, usecom, usencom, calcmode, nchangeand, lnter, lnur, threshold, polymask):
        if not self.active:
            return p
        # SBM I'm not sure if there's a prompt increment that isn't working, or that it must be done manually,
        # but either way this will force p.prompt to receive the next value rather than revert to orig in batchcount.
        # if self.imgcount + 1 < len(self.orig_all_prompts):
        #     p.prompt = p.all_prompts[self.imgcount + 1]
        #     p.negative_prompt = p.all_negative_prompts[self.imgcount + 1]
        # else:
        #     if self.usecom or self.cells or self.anded:
        #         p.prompt = self.orig_all_prompts[0]
        #         p.all_prompts[self.imgcount] = self.orig_all_prompts[self.imgcount]
        #     if self.usencom:
        #         p.negative_prompt = self.orig_all_negative_prompts[0]
        #         p.all_negative_prompts[self.imgcount] = self.orig_all_negative_prompts[self.imgcount]
        # self.imgcount += 1
        print("postprocess_image : ",self.imgcount,p.iteration,p.prompt,p.all_prompts)
        return p

    def postprocess(self, p, processed, *args):
        if self.active : 
            with open(os.path.join(paths.data_path, "params.txt"), "w", encoding="utf8") as file:
                processedx = Processed(p, [], p.seed, "")
                file.write(processedx.infotext(p, 0))
        
        if self.modep:
            savepmasks(self, processed)

        if self.debug : debugall(self)

        unloader(self, p)


    def denoiser_callback(self, params: CFGDenoiserParams):
        denoiser_callback_s(self, params)

    def denoised_callback(self, params: CFGDenoisedParams):
        denoised_callback_s(self, params)


def unloader(self,p):
    if hasattr(self,"handle"):
        print("unloaded")
        hook_forwards(self, p.sd_model.model.diffusion_model, remove=True)
        del self.handle

    self.__init__()
    
    shared.batch_cond_uncond = orig_batch_cond_uncond

    unloadlorafowards(p)

def denoiserdealer(self):
    if self.calcmode =="Latent": # prompt mode use only denoiser callbacks
        if not hasattr(self,"dd_callbacks"):
            self.dd_callbacks = on_cfg_denoised(self.denoised_callback)
        shared.batch_cond_uncond = False

    if not hasattr(self,"dr_callbacks"):
        self.dr_callbacks = on_cfg_denoiser(self.denoiser_callback)


############################################################
##### prompts, tokens
def commondealer(self, p, usecom, usencom):
    all_prompts = []
    all_negative_prompts = []
    def comadder(prompt):
        ppl = prompt.split(KEYBRK)
        for i in range(len(ppl)):
            if i == 0:
                continue
            ppl[i] = ppl[0] + ", " + ppl[i]
        ppl = ppl[1:]
        prompt = f"{KEYBRK} ".join(ppl)
        return prompt

    if usecom:
        self.prompt = p.prompt = comadder(p.prompt)
        for pr in p.all_prompts:
            all_prompts.append(comadder(pr))
        p.all_prompts = all_prompts

    if usencom:
        self.negative_prompt = p.negative_prompt = comadder(p.negative_prompt)
        for pr in p.all_negative_prompts:
            all_negative_prompts.append(comadder(pr))
        p.all_negative_prompts = all_negative_prompts
        
    return self, p


def anddealer(self, p, calcmode):
    self.divide = p.prompt.count(KEYBRK)
    if calcmode != "Latent" : return self, p

    p.prompt = p.prompt.replace(KEYBRK, "AND")
    for i in lange(p.all_prompts):
        p.all_prompts[i] = p.all_prompts[i].replace(KEYBRK, "AND")
    p.negative_prompt = p.negative_prompt.replace(KEYBRK, "AND")
    for i in lange(p.all_negative_prompts):
        p.all_negative_prompts[i] = p.all_negative_prompts[i].replace(KEYBRK, "AND")
    self.divide = p.prompt.count("AND") + 1
    return self, p


def tokendealer(self, p, seps):
    ppl = p.all_prompts[0].split(seps)
    npl = p.all_negative_prompts[0].split(seps)
    targets =[p.split(",")[-1] for p in ppl[1:]]
    pt, nt, ppt, pnt, tt = [], [], [], [], []

    padd = 0
    for pp in ppl:
        tokens, tokensnum = shared.sd_model.cond_stage_model.tokenize_line(pp)
        pt.append([padd, tokensnum // TOKENS + 1 + padd])
        ppt.append(tokensnum)
        padd = tokensnum // TOKENS + 1 + padd

    if self.modep:
        for target in targets:
            ptokens, tokensnum = shared.sd_model.cond_stage_model.tokenize_line(ppl[0])
            ttokens, _ = shared.sd_model.cond_stage_model.tokenize_line(target)

            i = 1
            tlist = []
            while ttokens[0].tokens[i] != 49407:
                if ttokens[0].tokens[i] in ptokens[0].tokens:
                    tlist.append(ptokens[0].tokens.index(ttokens[0].tokens[i]))
                i += 1
            if tlist != [] : tt.append(tlist)

    paddp = padd
    padd = 0
    for np in npl:
        _, tokensnum = shared.sd_model.cond_stage_model.tokenize_line(np)
        nt.append([padd, tokensnum // TOKENS + 1 + padd])
        pnt.append(tokensnum)
        padd = tokensnum // TOKENS + 1 + padd

    self.eq = paddp == padd

    self.pt = pt
    self.nt = nt
    self.pe = tt
    self.ppt = ppt
    self.pnt = pnt

    return self


def thresholddealer(self, p ,threshold):
    if self.modep:
        threshold = threshold.split(",")
        while len(self.pe) >= len(threshold) + 1:
            threshold.append(threshold[0])
        self.th = [floatdef(t, 0.4) for t in threshold] * self.batch_size
        if self.debug :print ("threshold", self.th)
    return self, p


def bratioprompt(self, bratios):
    if not self.modep: return self
    bratios = bratios.split(",")
    bratios = [floatdef(b, 0) for b in bratios]
    while len(self.pe) >= len(bratios) + 1:
        bratios.append(bratios[0])
    self.bratios = bratios
    return self
#####################################################
##### Save  and Load Settings

fcountbrk = lambda x: x.count(KEYBRK)
fint = lambda x: int(x)

# Json formatters.
fjstr = lambda x: x.strip()
#fjbool = lambda x: (x.upper() == "TRUE" or x.upper() == "T")
fjbool = lambda x: x # Json can store booleans reliably.

# (json_name, value_format, default)
# If default = none then will use current gradio value. 
PRESET_KEYS = [
("name",fjstr,"") , # Name is special, preset's key.
("mode", fjstr, None) ,
("ratios", fjstr, None) ,
("baseratios", fjstr, None) ,
("usebase", fjbool, None) ,
("usecom", fjbool, False) ,
("usencom", fjbool, False) ,
("calcmode", fjstr, "Attention") , # Generation mode.
("nchangeand", fjbool, False) ,
("lnter", fjstr, "0") ,
("lnur", fjstr, "0") ,
("threshold", fjstr, "0") ,
]


def savepresets(*settings):
    # NAME must come first.
    name = settings[0]
    path_root = modules.scripts.basedir()
    filepath = os.path.join(path_root, "scripts", "regional_prompter_presets.json")

    try:
        with open(filepath, mode='r', encoding="utf-8") as f:
            # presets = json.loads(json.load(f))
            presets = json.load(f)
            pr = {PRESET_KEYS[i][0]:settings[i] for i,_ in enumerate(PRESET_KEYS)}
            written = False
            # if name == "lastrun": # SBM We should check the preset is unique in any case.
            for i, preset in enumerate(presets):
                if name == preset["name"]:
                # if "lastrun" in preset["name"]:
                    presets[i] = pr
                    written = True
            if not written:
                presets.append(pr)
        with open(filepath, mode='w', encoding="utf-8") as f:
            # json.dump(json.dumps(presets), f, indent = 2)
            json.dump(presets, f, indent = 2)
    except Exception as e:
        print(e)

    presets = loadpresets(filepath)
    return gr.update(choices=[pr["name"] for pr in presets])


def loadpresets(filepath):
    presets = []
    try:
        with open(filepath, encoding="utf-8") as f:
            # presets = json.loads(json.load(f))
            presets = json.load(f)
    except OSError as e:
        print("Init / preset error.")
        presets = initpresets(filepath)
    except TypeError:
        print("Corrupted file, resetting.")
        presets = initpresets(filepath)
    except JSONDecodeError:
        print("Json file could not be decoded.")
        presets = initpresets(filepath)
        
    return presets


def initpresets(filepath):
    lpr = PRESETS
    # if not os.path.isfile(filepath):
    try:
        with open(filepath, mode='w', encoding="utf-8") as f:
            lprj = []
            for pr in lpr:
                plen = min(len(PRESET_KEYS), len(pr)) # Future setting additions ignored.
                prj = {PRESET_KEYS[i][0]:pr[i] for i in range(plen)}
                lprj.append(prj)
            #json.dump(json.dumps(lprj), f, indent = 2)
            json.dump(lprj, f, indent = 2)
            return lprj
    except Exception as e:
        return None

def debugall(self):
    print(f"mode : {self.calcmode}\ndivide : {self.mode}\nusebase : {self.usebase}")
    print(f"base ratios : {self.bratios}\nusecommon : {self.usecom}\nusenegcom : {self.usencom}\nuse 2D : {self.cells}")
    print(f"divide : {self.divide}\neq : {self.eq}\n")
    print(f"tokens : {self.ppt},{self.pnt},{self.pt},{self.nt}\n")
    print(f"ratios : {self.aratios}\n")
    print(f"prompt : {self.pe}\n")

def flagfromkeys(self, p):
    '''
    detect COMM/BASE keys and set flags
    '''
    if KEYCOMM in p.prompt:
        self.usecom = True
    
    if KEYCOMM in p.negative_prompt:
        self.usencom = True
    
    if KEYBASE in p.prompt:
        self.usebase = True

        
    if KEYPROMPT in p.prompt.upper():
        self.mode = "Prompt"
        p.replace(KEYPROMPT,KEYBRK)

    return self, p

def keyconverter(aratios,mode,usecom,usebase,p):
    '''convert BREAKS to ADDCOMM/ADDBASE/ADDCOL/ADDROW'''
    keychanger = makeimgtmp(aratios,mode,usecom,usebase,inprocess = True)
    keychanger = keychanger[:-1]
    print(keychanger,p.prompt)
    for change in keychanger:
        if change == KEYCOMM and KEYCOMM in p.prompt: continue
        if change == KEYBASE and KEYBASE in p.prompt: continue
        p.prompt= p.prompt.replace(KEYBRK,change,1)

    return p
