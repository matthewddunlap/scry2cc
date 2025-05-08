"""
Configuration for 8th Edition frames
"""

EIGHTH_FRAME = {
    "width": 2010,
    "height": 2814,
    "margin_x": 0,
    "margin_y": 0,
    "art_x": 0.0880597014925373,
    "art_y": 0.10199004975124377,
    "art_zoom": 2.941,
    "art_rotate": "0",
    "set_symbol_x": 0.8019900497512438,
    "set_symbol_y": 0.5689410092395167,
    "set_symbol_zoom": 0.343,
    "watermark_source": "img/blank.png",  # Path will be prefixed with protocol/host/port
    "watermark_x": 0,
    "watermark_y": 0,
    "watermark_zoom": 0,
    "watermark_left": "#b79d58",
    "watermark_right": "none",
    "watermark_opacity": 0.4,
    "frame_path_format": "/img/frames/{frame}/{color_code}.png",  # Frame image is PNG
    "mask_path_format": "/img/frames/{frame}/{mask_name}{ext}",   # Will add proper extension per mask
    "pt_path_format": "/img/frames/{frame}/pt/{color_code}.png",  # P/T box is PNG
    "land_color_format": "{color_code}.png",  # For lands - no special format in 8th
    "art_bounds": {
        "x": 0.088,
        "y": 0.12,
        "width": 0.824,
        "height": 0.4348
    },
    "set_symbol_bounds": {
        "x": 0.9047,
        "y": 0.5886,
        "width": 0.12,
        "height": 0.0391,
        "vertical": "center", # Assuming these are standard
        "horizontal": "right" # Assuming these are standard
    },
    "watermark_bounds": {
        "x": 0.5,
        "y": 0.7605,
        "width": 0.75,
        "height": 0.2305
    },
    "text": {
        "mana": {
            "name": "Mana Cost",
            "y": 0.0705,
            "width": 0.9147,
            "height": 0.030952380952380953,
            "oneLine": True,
            "size": 0.03968253968253968,
            "align": "right",
            "shadowX": -0.001,
            "shadowY": 0.0029,
            "manaCost": True,
            "manaSpacing": 0
        },
        "title": {
            "name": "Title",
            "x": 0.09,
            "y": 0.0629,
            "width": 0.824,
            "height": 0.0429,
            "oneLine": True,
            "font": "matrixb",
            "size": 0.0429
        },
        "type": {
            "name": "Type",
            "x": 0.1,
            "y": 0.572,
            "width": 0.8,
            "height": 0.0358,
            "oneLine": True,
            "font": "matrixb",
            "size": 0.0358
        },
        "rules": {
            "name": "Rules Text",
            "x": 0.1,
            "y": 0.6277,
            "width": 0.8,
            "height": 0.2691,
            "size": 0.0362
        },
        "pt": {
            "name": "Power/Toughness",
            "x": 0.7667,
            "y": 0.8953,
            "width": 0.1367,
            "height": 0.0443,
            "size": 0.0443,
            "font": "matrixbsc",
            "oneLine": True,
            "align": "center"
        }
    },
    "bottom_info": {
        "top": {
            "text": "{conditionalcolor:Black_Frame,Land_Frame,Colorless_Frame:white}￮ {elemidinfo-artist}",
            "x": 0.094,
            "y": 0.9228571428571428,
            "width": 0.8107,
            "height": 0.0248,
            "oneLine": True,
            "font": "matrixb",
            "size": 0.0248,
            "color": "black",
            "shadowX": 0.0007,
            "shadowY": 0.0005
        },
        "wizards": {
            "name": "wizards",
            "text": "{conditionalcolor:Black_Frame,Land_Frame,Colorless_Frame:white}™ & © 1993-{elemidinfo-year} Wizards of the Coast, Inc. {elemidinfo-number}",
            "x": 0.094,
            "y": 0.9323809523809524,
            "width": 0.8107,
            "height": 0.0153,
            "oneLine": True,
            "font": "mplantin",
            "size": 0.0153,
            "color": "black",
            "shadowX": 0.0007,
            "shadowY": 0.0005
        },
        "bottom": {
            "text": "{conditionalcolor:Black_Frame,Land_Frame,Colorless_Frame:white}NOT FOR SALE   CardConjurer.com",
            "x": 0.094,
            "y": 0.9495238095238095,
            "width": 0.8107,
            "height": 0.0134,
            "oneLine": True,
            "font": "mplantin",
            "size": 0.0134,
            "color": "black",
            "shadowX": 0.0007,
            "shadowY": 0.0005
        }
    },
    "uses_frame_set": False  # Indicates whether the frame uses a frameSet in paths
}
