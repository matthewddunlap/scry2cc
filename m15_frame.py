# --- file: m15_frame.py ---
"""
Configuration for M15 frames (Magic 2015 and similar style)
"""

M15_FRAME = {
    "width": 2010, # Consistent in Stangg & Hostage Taker
    "height": 2814, # Consistent
    "margin_x": 0,  # Consistent
    "margin_y": 0,  # Consistent

    # Art, Set Symbol, Watermark - these can vary per card, but the config
    # should provide sensible defaults or base values if CardConjurer uses them.
    # The JSON examples show instance-specific values.
    # For the config, we'll use Stangg's as a representative example,
    # but these are often overridden by CardBuilder using actual card data.
    "art_x": 0.07661691542288557,
    "art_y": 0.09168443496801705,
    "art_zoom": 3.027,
    "art_rotate": "0",

    "set_symbol_x": 0.8144278606965174,
    "set_symbol_y": 0.5707178393745558,
    "set_symbol_align_x_right": 0.9213333333333333,  # 1382 / 1500.0
    "set_symbol_align_y_center": 0.590952380952381,  # 1241 / 2100.0
    "set_symbol_zoom": 0.358, # This replaces your previous M15 default of 0.226 

    "watermark_source": "img/blank.png", # Default, path constructed later
    "watermark_x": 0.33880597014925373,
    "watermark_y": 0.6609808102345416,
    "watermark_zoom": 649, # Still seems large, but taken from JSON
    "watermark_left": "#b79d58",
    "watermark_right": "none",
    "watermark_opacity": 0.4,

    # --- Path Formats ---
    # Main frame pieces seem to use this distinct "custom/m15-eighth" path.
    "frame_path_format": "/img/frames/custom/m15-eighth/regular/{color_code}.png",
    # P/T boxes are in a more standard m15 path.
    "pt_path_format": "/img/frames/m15/regular/m15PT{color_code}.png",
    # Legendary crowns.
    "legend_crown_path_format": "/img/frames/m15/crowns/m15Crown{color_code}.png",
    # Masks for M15 frames.
    "mask_path_format": "/img/frames/m15/regular/m15Mask{mask_name}.png",
    # Specific masks that might not fit the pattern above (if any)
    "frame_mask_name_for_main_frame_layer": "Frame.png", # as seen in Stangg: "src": "/img/frames/custom/m15-eighth/regular/Frame.png"
    "border_mask_name_for_main_frame_layer": "Border.png", # as seen in Stangg: "src": "/img/frames/custom/m15-eighth/regular/Border.png"


    # --- Bounds --- (Taken from Stangg, assuming Hostage Taker is similar or these are M15 defaults)
    "art_bounds": {
        "x": 0.0767,
        "y": 0.1129,
        "width": 0.8476,
        "height": 0.4429
    },
    "set_symbol_bounds": {
        "x": 0.9213,
        "y": 0.591,
        "width": 0.12,
        "height": 0.04095238095238095,  # From 86/2100 in m15/version.js (use more precision)
        "vertical": "center", # Assuming these are standard
        "horizontal": "right" # Assuming these are standard
    },
    "watermark_bounds": {
        "x": 0.5,
        "y": 0.7762,
        "width": 0.75,
        "height": 0.2305
    },
    "legend_crown_bounds": { # From Stangg's crown layers
        "height": 0.1667,
        "width": 0.9454,
        "x": 0.0274,
        "y": 0.0191
    },
    "legend_crown_cover_bounds": { # From Stangg "Legend Crown Border Cover"
        "height": 0.0177,
        "width": 0.9214,
        "x": 0.0394,
        "y": 0.0277
    },
    "pt_bounds": { # From Stangg P/T layer
        "height": 0.0733,
        "width": 0.188,
        "x": 0.7573,
        "y": 0.9052380952380953
    },

    # --- Text Boxes --- (Taken from Stangg, Hostage Taker has identical structures and fonts)
    "text": {
        "mana": {
            "name": "Mana Cost", # Used for CardConjurer UI, not in final JSON frame struct
            # "text" field is populated by card_data
            "y": 0.0613,
            "width": 0.9292,
            "height": 0.03380952380952381,
            "oneLine": True,
            "size": 0.043345543345543344, # Font size
            "align": "right",
            "shadowX": -0.001,
            "shadowY": 0.0029,
            "manaCost": True, # CC specific flag
            "manaSpacing": 0  # CC specific flag
        },
        "title": {
            "name": "Title",
            # "text" field is populated by card_data
            "x": 0.0854,
            "y": 0.0522,
            "width": 0.8292,
            "height": 0.0543,
            "oneLine": True,
            "font": "belerenb",
            "size": 0.0381
        },
        "type": {
            "name": "Type",
            # "text" field is populated by card_data
            "x": 0.0854,
            "y": 0.5664,
            "width": 0.8292,
            "height": 0.0543,
            "oneLine": True,
            "font": "belerenb",
            "size": 0.0324
        },
        "rules": {
            "name": "Rules Text",
            # "text" field is populated by card_data
            "x": 0.086,
            "y": 0.6303,
            "width": 0.828,
            "height": 0.2875,
            "size": 0.0362 # Default font size for rules text
        },
        "pt": {
            "name": "Power/Toughness",
            # "text" field is populated by card_data
            "x": 0.7928,
            "y": 0.9223809523809524,
            "width": 0.1367,
            "height": 0.0372,
            "size": 0.0372,
            "font": "belerenbsc",
            "oneLine": True,
            "align": "center"
        }
    },

    # --- Bottom Info --- (Taken from Stangg, Hostage Taker identical)
    "bottom_info": {
        "top": {
            "text": "{conditionalcolor:M15_Border,Nyx_White_Frame,Nyx_Blue_Frame,Nyx_Black_Frame,Nyx_Red_Frame,Nyx_Green_Frame,Nyx_Multicolored_Frame,Nyx_Artifact_Frame,Black_Frame,Land_Frame,Colorless_Frame,Vehicle_Frame,White_Land_Frame,Blue_Land_Frame,Black_Land_Frame,Red_Land_Frame,Green_Land_Frame,Multicolored_Land_Frame:white}￮ {elemidinfo-artist}",
            "x": 0.0647,
            "y": 0.9395238095238095, # Still seems high, but consistent in examples
            "width": 0.8107,
            "height": 0.0248,
            "oneLine": True,
            "font": "belerenbsc",
            "size": 0.02095,
            "color": "black"
            # "shadowX", "shadowY" not present for this element in examples, but often are.
        },
        "wizards": {
            "name": "wizards", # UI name
            "text": "{conditionalcolor:M15_Border,Nyx_White_Frame,Nyx_Blue_Frame,Nyx_Black_Frame,Nyx_Red_Frame,Nyx_Green_Frame,Nyx_Multicolored_Frame,Nyx_Artifact_Frame,Black_Frame,Land_Frame,Colorless_Frame,Vehicle_Frame,White_Land_Frame,Blue_Land_Frame,Black_Land_Frame,Red_Land_Frame,Green_Land_Frame,Multicolored_Land_Frame:white}™ & © 1993-{elemidinfo-year} Wizards of the Coast, Inc. {elemidinfo-number}",
            "x": 0.0647,
            "y": 0.9323809523809524, # Still seems high
            "width": 0.8107,
            "height": 0.0153,
            "oneLine": True,
            "font": "mplantin",
            "size": 0.0153,
            "color": "black",
            "shadowX": 0.0007,
            "shadowY": 0.0005
        }
        # Add "bottom" if it exists for M15 (not seen in Stangg/Hostage Taker)
    },

    "uses_frame_set": False, # Paths like /img/frames/m15/crowns... don't have an intermediate 'regular' or 'promo' set folder.
                            # The 'custom/m15-eighth/regular' is part of the base path.
    "shows_flavor_bar": True, # Consistent "showsFlavorBar": true
    "version_string": "m15Eighth" # Consistent "version": "m15Eighth" in JSON
}
