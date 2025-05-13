# --- file: m15_frame.py ---
"""
Configuration for M15 frames (Magic 2015 and similar style) - REGULAR BORDERED version.
"""

M15_FRAME = {
    "width": 2010, 
    "height": 2814, 
    "margin_x": 0,  
    "margin_y": 0,  

    "art_x": 0.07661691542288557,
    "art_y": 0.09168443496801705,
    "art_zoom": 3.027,
    "art_rotate": "0",

    "set_symbol_x": 0.8144278606965174, 
    "set_symbol_y": 0.5707178393745558, 
    "set_symbol_zoom": 0.358,          
    "set_symbol_align_x_right": 0.9213333333333333,  
    "set_symbol_align_y_center": 0.590952380952381,  

    "watermark_source": "img/blank.png", 
    "watermark_x": 0.33880597014925373,
    "watermark_y": 0.6609808102345416,
    "watermark_zoom": 649, 
    "watermark_left": "#b79d58",
    "watermark_right": "none",
    "watermark_opacity": 0.4,

    # --- Path Formats for M15 Regular ---
    "frame_path_format": "/img/frames/custom/m15-eighth/regular/{color_code}.png", 
    "pt_path_format": "/img/frames/m15/regular/m15PT{color_code_upper}.png", 
    "legend_crown_path_format": "/img/frames/m15/crowns/m15Crown{color_code_upper}.png", 
    "legend_crown_cover_src": "/img/black.png", # For M15 regular
    "mask_path_format": "/img/frames/m15/regular/m15Mask{mask_name}.png",
    "frame_mask_name_for_main_frame_layer": "/img/frames/custom/m15-eighth/regular/Frame.png", 
    "border_mask_name_for_main_frame_layer": "/img/frames/custom/m15-eighth/regular/Border.png",
    # land_frame_path_format is not typically distinct for m15 regular; lands use frame_path_format with 'l', 'wl' etc.
    # land_color_format can be used if needed, but usually handled by ColorDetector returning specific land color codes.


    # --- Bounds for M15 Regular ---
    "art_bounds": { "x": 0.0767, "y": 0.1129, "width": 0.8476, "height": 0.4429 },
    "set_symbol_bounds": { "x": 0.9213, "y": 0.591, "width": 0.12, "height": 0.04095238095238095, "vertical": "center", "horizontal": "right" },
    "watermark_bounds": { "x": 0.5, "y": 0.7762, "width": 0.75, "height": 0.2305 },
    "legend_crown_bounds": { "height": 0.1667, "width": 0.9454, "x": 0.0274, "y": 0.0191 },
    "legend_crown_cover_bounds": { "height": 0.0177, "width": 0.9214, "x": 0.0394, "y": 0.0277 },
    "pt_bounds": { "height": 0.0733, "width": 0.188, "x": 0.7573, "y": 0.9052380952380953 }, # Consistent with m15ub from Stangg

    # --- Text Boxes for M15 Regular ---
    "text": {
        "mana": { "name": "Mana Cost", "y": 0.0613, "width": 0.9292, "height": 0.03380952380952381, "oneLine": True, "size": 0.043345543345543344, "align": "right", "shadowX": -0.001, "shadowY": 0.0029, "manaCost": True, "manaSpacing": 0 },
        "title": { "name": "Title", "x": 0.0854, "y": 0.0522, "width": 0.8292, "height": 0.0543, "oneLine": True, "font": "belerenb", "size": 0.0381 },
        "type": { "name": "Type", "x": 0.0854, "y": 0.5664, "width": 0.8292, "height": 0.0543, "oneLine": True, "font": "belerenb", "size": 0.0324 },
        "rules": { "name": "Rules Text", "x": 0.086, "y": 0.6303, "width": 0.828, "height": 0.2875, "size": 0.0362 },
        "pt": { "name": "Power/Toughness", "x": 0.7928, "y": 0.9223809523809524, "width": 0.1367, "height": 0.0372, "size": 0.0372, "font": "belerenbsc", "oneLine": True, "align": "center" }
    },

    # --- Bottom Info for M15 Regular ---
    "bottom_info": {
        "top": { "text": "{conditionalcolor:M15_Border,Nyx_White_Frame,Nyx_Blue_Frame,Nyx_Black_Frame,Nyx_Red_Frame,Nyx_Green_Frame,Nyx_Multicolored_Frame,Nyx_Artifact_Frame,Black_Frame,Land_Frame,Colorless_Frame,Vehicle_Frame,White_Land_Frame,Blue_Land_Frame,Black_Land_Frame,Red_Land_Frame,Green_Land_Frame,Multicolored_Land_Frame:white}\uFFEE {elemidinfo-artist}", "x": 0.0647, "y": 0.9395238095238095, "width": 0.8107, "height": 0.0248, "oneLine": True, "font": "belerenbsc", "size": 0.02095, "color": "black" },
        "wizards": { "name": "wizards", "text": "{conditionalcolor:M15_Border,Nyx_White_Frame,Nyx_Blue_Frame,Nyx_Black_Frame,Nyx_Red_Frame,Nyx_Green_Frame,Nyx_Multicolored_Frame,Nyx_Artifact_Frame,Black_Frame,Land_Frame,Colorless_Frame,Vehicle_Frame,White_Land_Frame,Blue_Land_Frame,Black_Land_Frame,Red_Land_Frame,Green_Land_Frame,Multicolored_Land_Frame:white}\u2122 & \u00a9 1993-{elemidinfo-year} Wizards of the Coast, Inc. {elemidinfo-number}", "x": 0.0647, "y": 0.9323809523809524, "width": 0.8107, "height": 0.0153, "oneLine": True, "font": "mplantin", "size": 0.0153, "color": "black", "shadowX": 0.0007, "shadowY": 0.0005 }
    },

    "uses_frame_set": False, 
    "shows_flavor_bar": True, 
    "version_string": "m15Eighth", # From packM15Eighth.js
    "noCorners": False # M15 regular usually has corners
}