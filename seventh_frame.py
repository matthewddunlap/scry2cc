"""
Configuration for 7th Edition frames
"""

SEVENTH_FRAME = {
    "width": 2010,
    "height": 2814,
    "margin_x": 0,
    "margin_y": 0,
    "art_x": 0.11641791044776119,
    "art_y": 0.09914712153518124,
    "art_zoom": 2.763,
    "art_rotate": "0",
    "set_symbol_x": 0.8383084577114428,
    "set_symbol_y": 0.5550817341862118,
    "set_symbol_zoom": 0.20600000000000002,
    "watermark_source": "img/blank.png",  # Path will be prefixed with protocol/host/port
    "watermark_x": 0.012437810945273632,
    "watermark_y": 0.5202558635394456,
    "watermark_zoom": 675,
    "watermark_left": "#b79d58",
    "watermark_right": "none",
    "watermark_opacity": 0.4,
    "frame_path_format": "/img/frames/{frame}/{frame_set}/{color_code}.png",
    "mask_path_format": "/img/frames/{frame}/{frame_set}/{mask_name}.svg",
    "land_color_format": "{color_code}l.png",  # For dual lands
    "art_bounds": {
        "x": 0.12,
        "y": 0.0991,
        "width": 0.7667,
        "height": 0.4429
    },
    "set_symbol_bounds": {
        "x": 0.9,
        "y": 0.5739,
        "width": 0.12,
        "height": 0.0372,
        "vertical": "center",
        "horizontal": "right"
    },
    "watermark_bounds": {
        "x": 0.18,
        "y": 0.64,
        "width": 0.64,
        "height": 0.24
    },
    "text": {
        "mana": {
            "name": "Mana Cost",
            "x": 0.1067,
            "y": 0.0539,
            "width": 0.8174,
            "height": 0.03428571428571429,
            "oneLine": True,
            "size": 0.04395604395604396,
            "align": "right",
            "manaCost": True,
            "fontSize": 0
        },
        "title": {
            "name": "Title",
            "x": 0.1134,
            "y": 0.0481,
            "width": 0.7734,
            "height": 0.041,
            "oneLine": True,
            "font": "goudymedieval",
            "size": 0.041,
            "color": "white",
            "shadowX": 0.002,
            "shadowY": 0.0015,
            "fontSize": 0
        },
        "type": {
            "name": "Type",
            "x": 0.1074,
            "y": 0.5486,
            "width": 0.7852,
            "height": 0.0543,
            "oneLine": True,
            "size": 0.032,
            "color": "white",
            "shadowX": 0.002,
            "shadowY": 0.0015,
            "fontSize": 0
        },
        "rules": {
            "name": "Rules Text",
            "x": 0.128,
            "y": 0.6067,
            "width": 0.744,
            "height": 0.2724,
            "size": 0.0358,
            "fontSize": 0
        },
        "pt": {
            "name": "Power/Toughness",
            "x": 0.8074,
            "y": 0.9043,
            "width": 0.1367,
            "height": 0.0429,
            "size": 0.0429,
            "oneLine": True,
            "align": "center",
            "color": "white",
            "shadowX": 0.002,
            "shadowY": 0.0015,
            "fontSize": 0
        }
    },
    "bottom_info": {
        "top": {
            "text": "Illus. {elemidinfo-artist}",
            "x": 0.1,
            "y": 0.9085714285714286,
            "width": 0.8,
            "height": 0.0267,
            "oneLine": True,
            "size": 0.0267,
            "align": "center",
            "shadowX": 0.0021,
            "shadowY": 0.0015,
            "color": "white"
        },
        "wizards": {
            "name": "wizards",
            "text": "™ & © {elemidinfo-year} Wizards of the Coast, Inc. {elemidinfo-number}",
            "x": 0.1,
            "y": 0.9204761904761904,
            "width": 0.8,
            "height": 0.0172,
            "oneLine": True,
            "size": 0.0172,
            "align": "center",
            "shadowX": 0.0014,
            "shadowY": 0.001,
            "color": "white"
        },
        "bottom": {
            "text": "NOT FOR SALE   CardConjurer.com",
            "x": 0.1,
            "y": 0.9395238095238095,
            "width": 0.8,
            "height": 0.012380952380952381,
            "oneLine": True,
            "size": 0.012380952380952381,
            "align": "center",
            "shadowX": 0.0014,
            "shadowY": 0.001,
            "color": "white"
        }
    },
    "uses_frame_set": True  # Indicates whether the frame uses a frameSet in paths
}
