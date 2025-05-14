# --- file: card_builder.py ---
"""
Module for building card data structure from Scryfall data
"""
import logging
from typing import Dict, List, Optional, Union
import io 
import re 
import json
import time

import requests 
from PIL import Image 
from lxml import etree 

from config import (
    ccProto, ccHost, ccPort,
    DEFAULT_INFO_YEAR, DEFAULT_INFO_RARITY, DEFAULT_INFO_SET,
    DEFAULT_INFO_LANGUAGE, DEFAULT_INFO_ARTIST, DEFAULT_INFO_NOTE, DEFAULT_INFO_NUMBER
)
from color_mapping import COLOR_CODE_MAP, RARITY_MAP

logger = logging.getLogger(__name__)

class CardBuilder:
    """Class for building card data from Scryfall data"""
    
    def __init__(self, frame_type: str, frame_config: Dict, frame_set: str = "regular", legendary_crowns: bool = False, auto_fit_art: bool = False, set_symbol_override: Optional[str] = None, auto_fit_set_symbol: bool = False, api_delay_seconds: float = 0.1):
        self.frame_type = frame_type
        self.frame_config = frame_config
        self.frame_set = frame_set
        self.legendary_crowns = legendary_crowns
        self.auto_fit_art = auto_fit_art
        self.set_symbol_override = set_symbol_override
        self.auto_fit_set_symbol = auto_fit_set_symbol
        self.api_delay_seconds = api_delay_seconds
        self.symbol_placement_lookup = {}
        if self.auto_fit_set_symbol: 
            try:
                with open("symbol_placements.json", "r") as f:
                    self.symbol_placement_lookup = json.load(f)
                logger.info(f"Loaded {len(self.symbol_placement_lookup)} entries from symbol_placements.json")
            except FileNotFoundError:
                logger.warning("symbol_placements.json not found. Auto-fit will use fallback calculations for all symbols.")
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding symbol_placements.json: {e}. Auto-fit will use fallback calculations.")

    def _extract_set_code_from_url(self, url: str) -> Optional[str]:
        if not url: return None
        match = re.search(r'/([\w]+)-[\w]+\.(svg|png)$', url.lower())
        if match: return match.group(1)
        else: logger.warning(f"Could not extract set_code from URL: {url}"); return None

    def _calculate_auto_fit_set_symbol_params(self, set_symbol_url: str) -> Optional[Dict[str, float]]:
        set_code = self._extract_set_code_from_url(set_symbol_url) 
        if set_code:
            lookup_key = f"{set_code}-{self.frame_type.lower()}" 
            if lookup_key in self.symbol_placement_lookup:
                fixed_params = self.symbol_placement_lookup[lookup_key]
                if isinstance(fixed_params, dict) and all(k in fixed_params for k in ('x', 'y', 'zoom')):
                    logger.info(f"Using fixed placement for '{lookup_key}' from lookup table: X={fixed_params['x']:.4f}, Y={fixed_params['y']:.4f}, Zoom={fixed_params['zoom']:.4f}")
                    return {"setSymbolX": fixed_params['x'], "setSymbolY": fixed_params['y'], "setSymbolZoom": fixed_params['zoom']}
                else: logger.warning(f"Invalid data structure for '{lookup_key}' in symbol_placements.json. Proceeding to fallback.")
            else: logger.info(f"No fixed placement found for '{lookup_key}' in lookup table. Proceeding to fallback calculation.")
        else: logger.warning(f"Could not extract set_code from URL '{set_symbol_url}' for lookup. Proceeding to fallback calculation.")

        try:
            response = requests.get(set_symbol_url, timeout=10); response.raise_for_status(); svg_bytes = response.content
            if self.api_delay_seconds > 0: time.sleep(self.api_delay_seconds)
            
            svg_dims = self._get_svg_dimensions(svg_bytes)
            if not svg_dims or svg_dims["width"] <= 0 or svg_dims["height"] <= 0: return None
            svg_intrinsic_width, svg_intrinsic_height = svg_dims["width"], svg_dims["height"]

            card_total_width = self.frame_config.get("width"); card_total_height = self.frame_config.get("height")
            symbol_bounds_config = self.frame_config.get("set_symbol_bounds")
            target_align_x_right_rel = self.frame_config.get("set_symbol_align_x_right")
            target_align_y_center_rel = self.frame_config.get("set_symbol_align_y_center")

            if not (card_total_width and card_total_height and symbol_bounds_config and isinstance(symbol_bounds_config, dict) and all(k in symbol_bounds_config for k in ('x','y','width', 'height')) and target_align_x_right_rel is not None and target_align_y_center_rel is not None):
                return {"setSymbolX": 0.9, "setSymbolY": 0.58, "setSymbolZoom": 0.3} 
            if card_total_width <= 0 or card_total_height <= 0: return None
            
            s_bound_rel_x = symbol_bounds_config["x"]; s_bound_rel_y = symbol_bounds_config["y"]
            s_bound_rel_width = symbol_bounds_config["width"]; s_bound_rel_height = symbol_bounds_config["height"]
            if s_bound_rel_width <= 0 or s_bound_rel_height <= 0: return None
            
            target_abs_symbol_box_width = s_bound_rel_width * card_total_width
            target_abs_symbol_box_height = s_bound_rel_height * card_total_height

            if svg_intrinsic_width <= 0 or svg_intrinsic_height <= 0: return None
            scale_x_factor = target_abs_symbol_box_width / svg_intrinsic_width
            scale_y_factor = target_abs_symbol_box_height / svg_intrinsic_height
            calculated_zoom = min(scale_x_factor, scale_y_factor)
            if calculated_zoom <= 1e-6: return None

            scaled_symbol_on_card_width_px = svg_intrinsic_width * calculated_zoom
            scaled_symbol_on_card_height_px = svg_intrinsic_height * calculated_zoom
                        
            scaled_symbol_width_rel = scaled_symbol_on_card_width_px / card_total_width
            scaled_symbol_height_rel = scaled_symbol_on_card_height_px / card_total_height
            calculated_set_symbol_x_relative = target_align_x_right_rel - scaled_symbol_width_rel
            scaled_symbol_half_height_rel = scaled_symbol_height_rel / 2.0
            calculated_set_symbol_y_relative = target_align_y_center_rel - scaled_symbol_half_height_rel
            return {"setSymbolX": calculated_set_symbol_x_relative, "setSymbolY": calculated_set_symbol_y_relative, "setSymbolZoom": calculated_zoom}
        except Exception as e: 
            logger.error(f"Error during fallback set symbol calculation for {set_symbol_url}: {e}", exc_info=True)
            return None

    def _get_svg_dimensions(self, svg_content_bytes: bytes) -> Optional[Dict[str, float]]:
        if not svg_content_bytes: return None
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            svg_root = etree.fromstring(svg_content_bytes, parser=parser)
            if not svg_root.tag.endswith('svg'): return None
            viewbox_str = svg_root.get("viewBox"); width_str = svg_root.get("width"); height_str = svg_root.get("height")
            intrinsic_width, intrinsic_height = None, None
            if viewbox_str:
                try:
                    parts = [float(p) for p in re.split(r'[,\s]+', viewbox_str.strip())]
                    if len(parts) == 4: intrinsic_width, intrinsic_height = parts[2], parts[3]
                except ValueError: pass
            if intrinsic_width is None and width_str and not width_str.endswith('%'):
                try: intrinsic_width = float(re.sub(r'[^\d\.]', '', width_str))
                except ValueError: pass
            if intrinsic_height is None and height_str and not height_str.endswith('%'):
                try: intrinsic_height = float(re.sub(r'[^\d\.]', '', height_str))
                except ValueError: pass
            if intrinsic_width and intrinsic_height and intrinsic_width > 0 and intrinsic_height > 0:
                return {"width": intrinsic_width, "height": intrinsic_height}
            return None
        except Exception: return None

    def _calculate_auto_fit_art_params(self, art_url: str) -> Optional[Dict[str, float]]:
        if not art_url: return None
        try:
            response = requests.get(art_url, timeout=10); response.raise_for_status()
            if self.api_delay_seconds > 0: time.sleep(self.api_delay_seconds) 
            img = Image.open(io.BytesIO(response.content)); art_natural_width, art_natural_height = img.width, img.height; img.close()
            if art_natural_width == 0 or art_natural_height == 0: return None
            card_total_width = self.frame_config.get("width"); card_total_height = self.frame_config.get("height")
            art_bounds_config = self.frame_config.get("art_bounds")
            if not (card_total_width and card_total_height and art_bounds_config and isinstance(art_bounds_config, dict) and all(k in art_bounds_config for k in ('x', 'y', 'width', 'height'))): return None
            if card_total_width <= 0 or card_total_height <= 0: return None
            art_box_relative_x = art_bounds_config.get("x", 0.0); art_box_relative_y = art_bounds_config.get("y", 0.0)
            art_box_relative_width = art_bounds_config.get("width", 0.0); art_box_relative_height = art_bounds_config.get("height", 0.0)
            if art_box_relative_width <= 0 or art_box_relative_height <= 0: return None
            target_abs_art_box_width = art_box_relative_width * card_total_width
            target_abs_art_box_height = art_box_relative_height * card_total_height
            if art_natural_width <= 0 or art_natural_height <= 0: return None
            scale_x = target_abs_art_box_width / art_natural_width; scale_y = target_abs_art_box_height / art_natural_height
            calculated_zoom = max(scale_x, scale_y)
            calculated_art_x = art_box_relative_x + (target_abs_art_box_width - art_natural_width * calculated_zoom) / 2 / card_total_width
            calculated_art_y = art_box_relative_y + (target_abs_art_box_height - art_natural_height * calculated_zoom) / 2 / card_total_height
            return {"artX": calculated_art_x, "artY": calculated_art_y, "artZoom": calculated_zoom}
        except Exception as e: logger.error(f"Error in _calculate_auto_fit_art_params for {art_url}: {e}", exc_info=True); return None
    
    def _format_path(self, path_format_str: Optional[str], **kwargs) -> str:
        if not path_format_str:
            # Log less verbosely if it's just a missing optional path like pt_path_format
            if not ('pt_path_format' in str(kwargs.get('caller_description', '')) and kwargs.get('path_type_optional', False)):
                 logger.error(f"Path format string is None or empty. Args: {kwargs}")
            return "/img/error_path.png" 
        
        valid_args = {k: v for k, v in kwargs.items() if f"{{{k}}}" in path_format_str}
        
        try:
            return path_format_str.format(**valid_args)
        except KeyError as e:
            logger.error(f"KeyError formatting path '{path_format_str}' with effectively used args {valid_args} (original args: {kwargs}): {e}")
            return "/img/error_path_key_error.png"
        except Exception as e_gen:
            logger.error(f"Generic error formatting path '{path_format_str}' with args {valid_args}: {e_gen}")
            return "/img/error_path_generic.png"

    def build_frame_path(self, color_code: str) -> str:
        return self._format_path(
            self.frame_config.get("frame_path_format"),
            caller_description="build_frame_path",
            frame=self.frame_type,
            frame_set=self.frame_set,
            color_code=color_code.lower()
        )
    
    def build_mask_path(self, mask_name: str) -> str:
        if self.frame_type == "8th": 
            ext = ".svg" if mask_name == "border" else ".png"
            return f"/img/frames/8th/{mask_name}{ext}"
        
        return self._format_path(
            self.frame_config.get("mask_path_format"),
            caller_description="build_mask_path",
            frame=self.frame_type,
            frame_set=self.frame_set,
            mask_name=mask_name
        )
    
    def build_land_frame_path(self, color_code: str) -> str:
        if self.frame_type == "8th":
            return f"/img/frames/8th/{color_code.lower()}l.png" 

        if self.frame_config.get("uses_frame_set", False): 
            base_dir = f"/img/frames/{self.frame_type}/{self.frame_set}/"
            land_filename_format = self.frame_config.get("land_color_format", "{color_code}l.png")
            return base_dir + land_filename_format.format(color_code=color_code.lower())

        if "land_frame_path_format" in self.frame_config: 
            return self._format_path(
                self.frame_config.get("land_frame_path_format"),
                caller_description="build_land_frame_path specific",
                color_code=color_code.lower() 
            )
        
        main_frame_path_format = self.frame_config.get("frame_path_format")
        if main_frame_path_format:
            base_dir = main_frame_path_format.rsplit('/', 1)[0] + "/"
            land_filename_format = self.frame_config.get("land_color_format", "{color_code}l.png")
            return base_dir + land_filename_format.format(color_code=color_code.lower())

        logger.error(f"Could not determine land frame path for {self.frame_type} with color {color_code}")
        return "/img/error_land_frame.png"

    def build_pt_frame_path(self, color_code: str) -> Optional[str]:
        return self._format_path(
            self.frame_config.get("pt_path_format"),
            caller_description="build_pt_frame_path", path_type_optional=True,
            frame=self.frame_type,
            color_code=color_code, 
            color_code_upper=color_code.upper(),
            color_code_lower=color_code.lower()
        )

    def build_m15_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:
        generated_frames = []
        card_name_for_logging = card_data.get('name', 'Unknown Card')
        is_legendary = 'Legendary' in card_data.get('type_line', '')
        if self.legendary_crowns and is_legendary:
            primary_crown_color_code, secondary_crown_color_code = None, None
            primary_crown_color_name, secondary_crown_color_name = "Legend", "Secondary"
            if isinstance(color_info, dict) and color_info.get('is_gold') and color_info.get('component_colors'):
                components = color_info['component_colors']
                if len(components) >= 1: primary_crown_color_code, primary_crown_color_name = components[0]['code'], components[0]['name']
                if len(components) >= 2: secondary_crown_color_code, secondary_crown_color_name = components[1]['code'], components[1]['name']
            elif isinstance(color_info, dict) and color_info.get('code'): 
                primary_crown_color_code, primary_crown_color_name = color_info['code'], color_info['name']
            
            if primary_crown_color_code:
                crown_path_format = self.frame_config.get("legend_crown_path_format") 
                crown_bounds = self.frame_config.get("legend_crown_bounds")
                cover_bounds = self.frame_config.get("legend_crown_cover_bounds") 
                if crown_path_format and crown_bounds and cover_bounds:
                    # M15 regular crowns use {color_code_upper} based on assets like m15CrownW.png
                    if secondary_crown_color_code:
                        generated_frames.append({"name": f"{secondary_crown_color_name} Legend Crown", "src": self._format_path(crown_path_format, color_code_upper=secondary_crown_color_code.upper()), "masks": [{"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}], "bounds": crown_bounds})
                    generated_frames.append({"name": f"{primary_crown_color_name} Legend Crown", "src": self._format_path(crown_path_format, color_code_upper=primary_crown_color_code.upper()), "masks": [], "bounds": crown_bounds})
                    generated_frames.append({"name": "Legend Crown Border Cover", "src": "/img/black.png", "masks": [], "bounds": cover_bounds})
            elif is_legendary: logger.warning(f"Could not determine color for M15 legendary crown on '{card_name_for_logging}'.")

        if 'power' in card_data and 'toughness' in card_data:
            pt_code, pt_name_prefix = None, "Unknown"
            if isinstance(color_info, dict) and color_info.get('is_gold'): pt_code, pt_name_prefix = COLOR_CODE_MAP['M']['code'], COLOR_CODE_MAP['M']['name']
            elif isinstance(color_info, dict) and color_info.get('code'): pt_code, pt_name_prefix = color_info['code'], color_info['name']
            if pt_code:
                pt_path = self.build_pt_frame_path(pt_code) 
                pt_bounds = self.frame_config.get("pt_bounds")
                if pt_path and pt_bounds and "/error_path" not in pt_path: generated_frames.append({"name": f"{pt_name_prefix} Power/Toughness", "src": pt_path, "masks": [], "bounds": pt_bounds})
        
        main_frame_layers = []; base_frame_path_fmt = self.frame_config.get("frame_path_format"); mask_path_fmt = self.frame_config.get("mask_path_format")
        main_frame_mask_src = self.frame_config.get("frame_mask_name_for_main_frame_layer"); main_border_mask_src = self.frame_config.get("border_mask_name_for_main_frame_layer")
        if not all([base_frame_path_fmt, mask_path_fmt, main_frame_mask_src, main_border_mask_src]): return generated_frames

        primary_color_code, primary_color_name = None, "Unknown"; secondary_color_code, secondary_color_name = None, None
        base_multicolor_code = COLOR_CODE_MAP['M']['code']; base_multicolor_name = COLOR_CODE_MAP['M']['name']
        ttfb_code, ttfb_name = None, None 
        is_land = isinstance(color_info, list)
        if is_land:
            ttfb_code, ttfb_name = COLOR_CODE_MAP['L']['code'], COLOR_CODE_MAP['L']['name']
            if len(color_info) > 1: primary_color_code, primary_color_name = color_info[1]['code'], color_info[1]['name']
            if len(color_info) > 2: secondary_color_code, secondary_color_name = color_info[2]['code'], color_info[2]['name']
            if not primary_color_code and len(color_info) == 1: primary_color_code, primary_color_name = color_info[0]['code'], color_info[0]['name']
        elif isinstance(color_info, dict): 
            if color_info.get('is_gold') and color_info.get('component_colors'):
                components = color_info['component_colors']
                if len(components) >= 1: primary_color_code, primary_color_name = components[0]['code'], components[0]['name']
                if len(components) >= 2: secondary_color_code, secondary_color_name = components[1]['code'], components[1]['name']
                ttfb_code, ttfb_name = base_multicolor_code, base_multicolor_name
            elif color_info.get('code'): primary_color_code, primary_color_name = color_info['code'], color_info['name']; ttfb_code, ttfb_name = primary_color_code, primary_color_name
        if not primary_color_code or not ttfb_code: return generated_frames
        
        src_primary = self._format_path(base_frame_path_fmt, color_code=primary_color_code)
        src_secondary = self._format_path(base_frame_path_fmt, color_code=secondary_color_code) if secondary_color_code else None
        src_ttfb = self._format_path(base_frame_path_fmt, color_code=ttfb_code)
        pinline_mask = self._format_path(mask_path_fmt, mask_name="Pinline")
        type_mask = self._format_path(mask_path_fmt, mask_name="Type")
        title_mask = self._format_path(mask_path_fmt, mask_name="Title")
        rules_mask = self._format_path(mask_path_fmt, mask_name="Rules")

        if secondary_color_code and src_secondary and "/error_path" not in src_secondary: 
            main_frame_layers.extend([
                {"name": f"{secondary_color_name} Frame", "src": src_secondary, "masks": [{"src": pinline_mask, "name": "Pinline"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},
                {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": pinline_mask, "name": "Pinline"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": type_mask, "name": "Type"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": title_mask, "name": "Title"}]},
                {"name": f"{secondary_color_name} Frame", "src": src_secondary, "masks": [{"src": rules_mask, "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},
                {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": rules_mask, "name": "Rules"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": main_frame_mask_src, "name": "Frame"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": main_border_mask_src, "name": "Border"}]}])
        else: 
            main_frame_layers.extend([
                {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": pinline_mask, "name": "Pinline"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": type_mask, "name": "Type"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": title_mask, "name": "Title"}]},
                {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": rules_mask, "name": "Rules"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": main_frame_mask_src, "name": "Frame"}]},
                {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": main_border_mask_src, "name": "Border"}]}])
        generated_frames.extend(main_frame_layers)
        return generated_frames

    def build_eighth_edition_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:
        generated_frames = []
        if 'power' in card_data and 'toughness' in card_data:
            pt_color_code, pt_name_prefix = None, None
            if isinstance(color_info, dict):
                code, name = color_info.get('code'), color_info.get('name')
                if code == 'a': pt_color_code, pt_name_prefix = 'a', "Artifact"
                elif code == 'm': pt_color_code, pt_name_prefix = 'm', "Gold"
                elif code in ['w', 'u', 'b', 'r', 'g', 'c']: pt_color_code, pt_name_prefix = code, name
            if pt_color_code and pt_name_prefix:
                pt_path = self.build_pt_frame_path(pt_color_code)
                if pt_path and "/error_path" not in pt_path: generated_frames.append({"name": f"{pt_name_prefix} Power/Toughness", "src": pt_path, "masks": [], "bounds": {"height": 0.0839, "width": 0.2147, "x": 0.7227, "y": 0.8796}})
        main_frame_layers = []
        if isinstance(color_info, list): 
            land_base_frame_info = color_info[0]
            base_src = self.build_frame_path(land_base_frame_info['code'])
            if len(color_info) > 2: 
                first_cc, second_cc = color_info[1]['code'], color_info[2]['code']
                first_cn, second_cn = color_info[1]['name'], color_info[2]['name']
                main_frame_layers.extend([
                    {"name": f"{second_cn} Land Frame", "src": self.build_land_frame_path(second_cc), "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},
                    {"name": f"{first_cn} Land Frame", "src": self.build_land_frame_path(first_cc), "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("type"), "name": "Type"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("title"), "name": "Title"}]},
                    {"name": f"{second_cn} Land Frame", "src": self.build_land_frame_path(second_cc), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},
                    {"name": f"{first_cn} Land Frame", "src": self.build_land_frame_path(first_cc), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]}])
            elif len(color_info) > 1: 
                mana_color = color_info[1]
                main_frame_layers.extend([
                    {"name": f"{mana_color['name']} Land Frame", "src": self.build_land_frame_path(mana_color['code']), "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("type"), "name": "Type"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("title"), "name": "Title"}]},
                    {"name": f"{mana_color['name']} Land Frame", "src": self.build_land_frame_path(mana_color['code']), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]}])
            else: main_frame_layers.extend([{"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize()}]} for mask_name in ["pinline", "type", "title", "rules", "frame", "border"]])
        elif isinstance(color_info, dict): 
            main_frame_color_code, main_frame_color_name = color_info.get('code'), color_info.get('name')
            if main_frame_color_code and main_frame_color_name:
                main_frame_src = self.build_frame_path(main_frame_color_code)
                main_frame_layers.extend([{"name": f"{main_frame_color_name} Frame", "src": main_frame_src, "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize()}]} for mask_name in ["pinline", "type", "title", "rules", "frame", "border"]])
        generated_frames.extend(main_frame_layers)
        return generated_frames
    
    def build_seventh_edition_frames(self, color_info, card_data: Dict) -> List[Dict]:
        frames = []
        common_masks = ["frame", "trim", "border"] 
        if isinstance(color_info, list): 
            land_frame = color_info[0]
            if len(color_info) > 2: 
                first_color, second_color = color_info[1], color_info[2]
                frames.append({"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]})
                frames.append({"name": f"{second_color['name']} Land Frame", "src": self.build_land_frame_path(second_color['code']), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]})
                frames.append({"name": f"{first_color['name']} Land Frame", "src": self.build_land_frame_path(first_color['code']), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]})
                frames.extend([{"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize() if mask_name != "trim" else "Textbox Pinline"}]} for mask_name in common_masks])
            elif len(color_info) > 1: 
                color = color_info[1]
                frames.append({"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]})
                frames.append({"name": f"{color['name']} Land Frame", "src": self.build_land_frame_path(color['code']), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]})
                frames.extend([{"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize() if mask_name != "trim" else "Textbox Pinline"}]} for mask_name in common_masks])
            else: frames = [{"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize() if mask_name != "trim" else "Textbox Pinline"}]} for mask_name in ["pinline", "rules"] + common_masks]
        else: 
            color_code, color_name = color_info['code'], color_info['name']
            frames = [{"name": f"{color_name} Frame", "src": self.build_frame_path(color_code), "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize() if mask_name != "trim" else "Textbox Pinline"}]} for mask_name in ["pinline", "rules"] + common_masks]
        return frames

# --- In card_builder.py ---
# Ensure these are imported at the top of card_builder.py if not already:
# from typing import Dict, List, Optional, Union
# from .color_mapping import COLOR_CODE_MAP # Assuming color_mapping is in the same package or adjust import
# import logging
# logger = logging.getLogger(__name__)

    # --- METHOD with fixes for non-basic lands ---
# --- In card_builder.py ---
# Ensure these are imported at the top of card_builder.py if not already:
# from typing import Dict, List, Optional, Union
# from .color_mapping import COLOR_CODE_MAP # Assuming color_mapping is in the same package or adjust import
# import logging
# logger = logging.getLogger(__name__)

    # --- METHOD with fixes for non-basic lands ---
# --- In card_builder.py ---
# Ensure these are imported at the top of card_builder.py if not already:
# from typing import Dict, List, Optional, Union
# from .color_mapping import COLOR_CODE_MAP # Assuming color_mapping is in the same package or adjust import
# import logging
# logger = logging.getLogger(__name__)

    def build_m15ub_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:
        """Build frames for M15 Unbordered (m15ub) cards."""
        generated_frames = []
        card_name_for_logging = card_data.get('name', 'Unknown Card')
        type_line = card_data.get('type_line', '')
        is_land_card = 'Land' in type_line

        # --- 1. Power/Toughness Box (if applicable) ---
        if 'power' in card_data and 'toughness' in card_data:
            pt_code_to_use = None 
            pt_name_prefix = "Unknown"
            
            # logger.debug(f"PT Check for '{card_name_for_logging}': Type='{type_line}', ColorInfo='{color_info}'") # Debug
            if 'Vehicle' in type_line:
                pt_code_to_use = COLOR_CODE_MAP.get('V', {}).get('code')
                pt_name_prefix = COLOR_CODE_MAP.get('V', {}).get('name', "Vehicle")
            elif isinstance(color_info, dict):
                is_gold_card = color_info.get('is_gold', False)
                is_artifact_card = color_info.get('is_artifact', False) 
                is_true_colorless_non_artifact = color_info.get('code') == COLOR_CODE_MAP.get('C',{}).get('code') and not is_artifact_card
                if is_gold_card: pt_code_to_use = COLOR_CODE_MAP.get('M', {}).get('code'); pt_name_prefix = COLOR_CODE_MAP.get('M', {}).get('name', "Multicolored")
                elif is_artifact_card: pt_code_to_use = COLOR_CODE_MAP.get('A', {}).get('code'); pt_name_prefix = COLOR_CODE_MAP.get('A', {}).get('name', "Artifact")
                elif is_true_colorless_non_artifact: pt_code_to_use = COLOR_CODE_MAP.get('C', {}).get('code'); pt_name_prefix = COLOR_CODE_MAP.get('C', {}).get('name', "Colorless")
                elif color_info.get('code') in ['w','u','b','r','g']: pt_code_to_use = color_info['code']; pt_name_prefix = color_info['name']
            
            if pt_code_to_use:
                pt_path_format_str = self.frame_config.get("pt_path_format")
                pt_bounds_config = self.frame_config.get("pt_bounds")
                pt_path = None 
                if not pt_path_format_str: logger.error(f"PT Error: pt_path_format missing in frame_config for {self.frame_type}")
                else: pt_path = self.build_pt_frame_path(pt_code_to_use) 
                
                if pt_path and pt_bounds_config and "/error_path" not in pt_path: 
                    generated_frames.append({"name": f"{pt_name_prefix} Power/Toughness", "src": pt_path, "masks": [], "bounds": pt_bounds_config})
        
        # --- 2. Legendary Crown (if applicable) ---
        is_legendary = 'Legendary' in type_line
        if self.legendary_crowns and is_legendary:
            primary_crown_color_code, secondary_crown_color_code = None, None
            primary_crown_color_name, secondary_crown_color_name = "Legend", "Secondary"
            if isinstance(color_info, dict) and color_info.get('is_gold') and color_info.get('component_colors'):
                components = color_info['component_colors']
                if len(components) >= 1: primary_crown_color_code, primary_crown_color_name = components[0]['code'], components[0]['name']
                if len(components) >= 2: secondary_crown_color_code, secondary_crown_color_name = components[1]['code'], components[1]['name']
            elif isinstance(color_info, dict) and color_info.get('code'): primary_crown_color_code, primary_crown_color_name = color_info['code'], color_info['name']
            elif is_land_card:
                if len(color_info) > 1 and 'code' in color_info[1]: primary_crown_color_code, primary_crown_color_name = color_info[1]['code'], color_info[1]['name']
                if len(color_info) > 2 and 'code' in color_info[2]: secondary_crown_color_code, secondary_crown_color_name = color_info[2]['code'], color_info[2]['name']
                elif not primary_crown_color_code and len(color_info) == 1 and 'code' in color_info[0]: primary_crown_color_code, primary_crown_color_name = color_info[0]['code'], color_info[0]['name']
            
            if primary_crown_color_code:
                crown_src_path_format = self.frame_config.get("legend_crown_path_format_m15ub")
                crown_bounds = self.frame_config.get("legend_crown_bounds")
                crown_cover_src = self.frame_config.get("legend_crown_cover_src", "/img/black.png")
                crown_cover_bounds = self.frame_config.get("legend_crown_cover_bounds")
                if crown_src_path_format and crown_bounds and crown_cover_src and crown_cover_bounds:
                    formatted_crown_path_secondary = self._format_path(crown_src_path_format, color_code_upper=secondary_crown_color_code.upper()) if secondary_crown_color_code else None
                    formatted_crown_path_primary = self._format_path(crown_src_path_format, color_code_upper=primary_crown_color_code.upper())
                    if secondary_crown_color_code and formatted_crown_path_secondary and "/error_path" not in formatted_crown_path_secondary:
                        generated_frames.append({"name": f"{secondary_crown_color_name} Legend Crown", "src": formatted_crown_path_secondary, "masks": [{"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}], "bounds": crown_bounds})
                    if formatted_crown_path_primary and "/error_path" not in formatted_crown_path_primary:
                        generated_frames.append({"name": f"{primary_crown_color_name} Legend Crown", "src": formatted_crown_path_primary, "masks": [], "bounds": crown_bounds})
                        generated_frames.append({"name": "Legend Crown Border Cover", "src": crown_cover_src, "masks": [], "bounds": crown_cover_bounds})
        
        # --- 3. Main Card Frame Layers ---
        main_frame_layers = []
        base_frame_path_fmt = self.frame_config.get("frame_path_format") 
        land_frame_path_fmt = self.frame_config.get("land_frame_path_format") 
        mask_path_fmt = self.frame_config.get("mask_path_format")

        if not all([base_frame_path_fmt, land_frame_path_fmt, mask_path_fmt]):
            logger.error(f"M15UB MainFrame: Essential path formats missing in config for '{card_name_for_logging}'.")
            generated_frames.extend(main_frame_layers); return generated_frames

        pinline_mask_src = self._format_path(mask_path_fmt, mask_name="Pinline")
        type_mask_src = self._format_path(mask_path_fmt, mask_name="Type")
        title_mask_src = self._format_path(mask_path_fmt, mask_name="Title")
        rules_mask_src = self._format_path(mask_path_fmt, mask_name="Rules")
        frame_mask_src = self.frame_config.get("frame_mask_name_for_main_frame_layer") 
        border_mask_src = self.frame_config.get("border_mask_name_for_main_frame_layer")

        primary_color_code_main, secondary_color_code_main = None, None 
        primary_color_name_main, secondary_color_name_main = "Unknown", None
        ttfb_code, ttfb_name = None, None 

        base_codes = { k: COLOR_CODE_MAP.get(k, {}).get('code') for k in ['M', 'L', 'A', 'V', 'C'] }
        base_names = { k: COLOR_CODE_MAP.get(k, {}).get('name') for k in ['M', 'L', 'A', 'V', 'C'] }

        # --- START DETAILED LOGGING FOR LANDS ---
        if card_name_for_logging in ["Strip Mine", "Tolaria"]: # Log only for these specific cards
            logger.info(f"--- Debugging {card_name_for_logging} in m15ub Main Frames ---")
            logger.info(f"is_land_card: {is_land_card}")
            logger.info(f"color_info: {color_info}")

        if is_land_card:
            ttfb_code, ttfb_name = base_codes.get('L'), base_names.get('L', "Land")
            if len(color_info) > 1 and 'code' in color_info[1]: 
                primary_color_code_main, primary_color_name_main = color_info[1]['code'], color_info[1]['name']
                if len(color_info) > 2 and 'code' in color_info[2]: 
                    secondary_color_code_main, secondary_color_name_main = color_info[2]['code'], color_info[2]['name']
            elif len(color_info) == 1 and 'code' in color_info[0]: 
                primary_color_code_main, primary_color_name_main = color_info[0]['code'], color_info[0]['name']
            else: # Problematic case for lands if color_info isn't as expected
                logger.warning(f"Unexpected color_info structure for land '{card_name_for_logging}': {color_info}. Defaulting primary_color_code_main to 'l'.")
                primary_color_code_main, primary_color_name_main = base_codes.get('L'), base_names.get('L', "Land")


            if card_name_for_logging in ["Strip Mine", "Tolaria"]:
                logger.info(f"Derived for Land: primary_color_code_main='{primary_color_code_main}', ttfb_code='{ttfb_code}'")
        
        # ... (rest of non-land color code determination) ...
        # This part should be the same as before
        elif isinstance(color_info, dict):
            if color_info.get('is_gold'):
                components = color_info.get('component_colors', [])
                if len(components) >= 1: primary_color_code_main, primary_color_name_main = components[0]['code'], components[0]['name']
                if len(components) >= 2: secondary_color_code_main, secondary_color_name_main = components[1]['code'], components[1]['name']
                ttfb_code, ttfb_name = base_codes.get('M'), base_names.get('M', "Multicolored")
            elif color_info.get('is_vehicle'): 
                primary_color_code_main, primary_color_name_main = base_codes.get('V'), base_names.get('V', "Vehicle")
                ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
            elif color_info.get('is_artifact'): 
                primary_color_code_main, primary_color_name_main = base_codes.get('A'), base_names.get('A', "Artifact")
                ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
            elif color_info.get('code') == base_codes.get('C'): 
                primary_color_code_main, primary_color_name_main = base_codes.get('C'), base_names.get('C', "Colorless")
                ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
            elif color_info.get('code'): 
                primary_color_code_main, primary_color_name_main = color_info['code'], color_info['name']
                ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
        
        if not primary_color_code_main : 
            logger.error(f"M15UB MainFrame: Primary color code MAIN missing for '{card_name_for_logging}'. color_info: {color_info}"); 
            generated_frames.extend(main_frame_layers); return generated_frames # Added color_info to log
        if not ttfb_code: 
            logger.warning(f"M15UB MainFrame: TTFB code missing for '{card_name_for_logging}', falling back to primary. color_info: {color_info}"); # Added color_info
            ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main 

        # Path determination logic (from previous correct version)
        src_pinline_rules = ""
        src_type_title = ""
        src_frame_border = ""

        if is_land_card:
            if primary_color_code_main != base_codes.get('L'): 
                src_pinline_rules = self._format_path(land_frame_path_fmt, color_code=primary_color_code_main) 
                src_type_title = src_pinline_rules 
            else: 
                src_pinline_rules = self._format_path(base_frame_path_fmt, color_code=primary_color_code_main) 
                src_type_title = src_pinline_rules 
            src_frame_border = self._format_path(base_frame_path_fmt, color_code=base_codes.get('L')) 
        else: 
            src_pinline_rules = self._format_path(base_frame_path_fmt, color_code=primary_color_code_main)
            src_type_title = self._format_path(base_frame_path_fmt, color_code=ttfb_code)
            src_frame_border = src_type_title 

        if card_name_for_logging in ["Strip Mine", "Tolaria"]:
            logger.info(f"Paths for {card_name_for_logging}: src_pinline_rules='{src_pinline_rules}', src_type_title='{src_type_title}', src_frame_border='{src_frame_border}'")
        # --- END DETAILED LOGGING FOR LANDS ---

        if "/error_path" in src_pinline_rules or "/error_path" in src_type_title or "/error_path" in src_frame_border :
            logger.error(f"M15UB MainFrame: Error in generating critical frame paths for '{card_name_for_logging}'.")
            generated_frames.extend(main_frame_layers); return generated_frames

        type_title_name_prefix = primary_color_name_main if (is_land_card and primary_color_code_main != base_codes.get('L')) else ttfb_name

        if secondary_color_code_main: 
            src_secondary_pinline_rules = self._format_path(land_frame_path_fmt if is_land_card else base_frame_path_fmt, color_code=secondary_color_code_main)
            
            if "/error_path" not in src_secondary_pinline_rules:
                main_frame_layers.extend([
                    {"name": f"{secondary_color_name_main} Frame", "src": src_secondary_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},
                    {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}]},
                    {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": type_mask_src, "name": "Type"}]},
                    {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": title_mask_src, "name": "Title"}]},
                    {"name": f"{secondary_color_name_main} Frame", "src": src_secondary_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},
                    {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}]},
                    {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask_src, "name": "Frame"}]},
                    {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask_src, "name": "Border"}]}])
            else: 
                logger.error(f"M15UB MainFrame: Error generating secondary path for '{card_name_for_logging}'. Falling back to primary layers.")
                main_frame_layers.extend([
                    {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}]},
                    {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": type_mask_src, "name": "Type"}]},
                    {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": title_mask_src, "name": "Title"}]},
                    {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}]},
                    {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask_src, "name": "Frame"}]},
                    {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask_src, "name": "Border"}]}])
        else: 
            main_frame_layers.extend([
                {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}]},
                {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": type_mask_src, "name": "Type"}]},
                {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": title_mask_src, "name": "Title"}]},
                {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}]},
                {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask_src, "name": "Frame"}]},
                {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask_src, "name": "Border"}]}])
        
        generated_frames.extend(main_frame_layers)
        return generated_frames
    
    def build_card_data(self, card_name: str, card_data: Dict, color_info) -> Dict:
        logger.debug(f"build_card_data for '{card_name}', frame_type '{self.frame_type}'. Keys in card_data: {list(card_data.keys())}")
        if 'power' in card_data and 'toughness' in card_data:
            logger.debug(f"P/T found in card_data for '{card_name}': P={card_data.get('power')}, T={card_data.get('toughness')}")
        else:
            logger.debug(f"P/T NOT found in card_data for '{card_name}'. 'power' present: {'power' in card_data}, 'toughness' present: {'toughness' in card_data}")

        frames_for_card_obj = []
        if self.frame_type == "8th": frames_for_card_obj = self.build_eighth_edition_frames(color_info, card_data)
        elif self.frame_type == "m15": frames_for_card_obj = self.build_m15_frames(color_info, card_data)
        elif self.frame_type == "m15ub": frames_for_card_obj = self.build_m15ub_frames(color_info, card_data)
        else: frames_for_card_obj = self.build_seventh_edition_frames(color_info, card_data)
        
        mana_symbols = []
        if isinstance(color_info, list) or self.frame_config.get("version_string", "") == "m15EighthSnow": 
            mana_symbols = ["/js/frames/manaSymbolsFAB.js", "/js/frames/manaSymbolsBreakingNews.js"] # Corrected case
            if self.frame_type == "seventh": mana_symbols = ["/js/frames/manaSymbolsFuture.js", "/js/frames/manaSymbolsOld.js"]

        # --- START: FLAVOR TEXT INTEGRATION (NEW LOGIC) ---
        oracle_text_from_scryfall = card_data.get('oracle_text', '')
        flavor_text_from_scryfall = card_data.get('flavor_text') # Will be None if key doesn't exist

        final_rules_text = oracle_text_from_scryfall
        # Determine initial showsFlavorBar based on frame config, may be overridden
        shows_flavor_bar_for_this_card = self.frame_config.get("shows_flavor_bar", False) 

        if flavor_text_from_scryfall:
            # Clean the flavor text: remove asterisks
            cleaned_flavor_text = flavor_text_from_scryfall.replace('*', '')
            
            if final_rules_text: # If there's oracle text, add a newline before {flavor}
                final_rules_text += "{flavor}" + cleaned_flavor_text
            else: # If no oracle text, just start with {flavor}
                final_rules_text = "{flavor}" + cleaned_flavor_text
            
            shows_flavor_bar_for_this_card = True # Override to true if flavor text is present
        # --- END: FLAVOR TEXT INTEGRATION ---

        set_code_from_scryfall = card_data.get('set', DEFAULT_INFO_SET)
        rarity_from_scryfall = card_data.get('rarity', 'c')
        rarity_code_for_symbol = RARITY_MAP.get(rarity_from_scryfall, rarity_from_scryfall)
        set_code_for_symbol_url = self.set_symbol_override.lower() if self.set_symbol_override else set_code_from_scryfall.lower()
        artist_name = card_data.get('artist', DEFAULT_INFO_ARTIST)
        
        art_crop_url = ""
        if 'image_uris' in card_data and 'art_crop' in card_data['image_uris']: art_crop_url = card_data['image_uris']['art_crop']
        elif 'card_faces' in card_data and card_data['card_faces']:
            for face in card_data['card_faces']:
                if 'image_uris' in face and 'art_crop' in face['image_uris']: art_crop_url = face['image_uris']['art_crop']; break

        art_x = self.frame_config.get("art_x", 0.0); art_y = self.frame_config.get("art_y", 0.0)
        art_zoom = self.frame_config.get("art_zoom", 1.0); art_rotate = self.frame_config.get("art_rotate", "0")
        if self.auto_fit_art and art_crop_url:
            auto_fit_params = self._calculate_auto_fit_art_params(art_crop_url) # Corrected: art_crop_url
            if auto_fit_params: art_x, art_y, art_zoom = auto_fit_params["artX"], auto_fit_params["artY"], auto_fit_params["artZoom"]
        
        set_symbol_x = self.frame_config.get("set_symbol_x", 0.0); set_symbol_y = self.frame_config.get("set_symbol_y", 0.0); set_symbol_zoom = self.frame_config.get("set_symbol_zoom", 0.1)
        # scryfall_set_for_symbol = card_data.get('set', 'lea').lower() # Not needed here, actual_set_code_for_url covers it
        scryfall_rarity_for_symbol = RARITY_MAP.get(card_data.get('rarity', 'c').lower(), card_data.get('rarity', 'c').lower())
        actual_set_code_for_url = set_code_for_symbol_url 
        set_symbol_source_url = f"{ccProto}://{ccHost}:{ccPort}/img/setSymbols/official/{actual_set_code_for_url}-{scryfall_rarity_for_symbol}.svg"
        if self.auto_fit_set_symbol and set_symbol_source_url:
            auto_fit_symbol_params = self._calculate_auto_fit_set_symbol_params(set_symbol_source_url)
            if auto_fit_symbol_params: set_symbol_x, set_symbol_y, set_symbol_zoom = auto_fit_symbol_params["setSymbolX"], auto_fit_symbol_params["setSymbolY"], auto_fit_symbol_params["setSymbolZoom"]

        # --- P/T Text Construction with Asterisk Replacement for 8th frame ---
        power_val = card_data.get('power', '')
        toughness_val = card_data.get('toughness', '')
        pt_text_final = ""

        if 'power' in card_data and 'toughness' in card_data: # Ensure both keys exist, even if values are empty or *
            # For 8th edition, replace standard asterisk with a Unicode alternative
            if self.frame_type == "8th":
                # Choose your preferred Unicode asterisk that renders well with matrixbsc
                replacement_asterisk = "X"
                # Option 1: Asterisk Operator (U+2217)
                # replacement_asterisk = "\u2217" 
                # Option 2: Low Asterisk (U+204E)
                # replacement_asterisk = "\u204E" 
                
                if power_val == "*":
                    power_val = replacement_asterisk
                if toughness_val == "*":
                    toughness_val = replacement_asterisk
            
            pt_text_final = f"{power_val}/{toughness_val}"
        # --- End P/T Text Construction ---         

        card_obj = {"key": card_name, "data": { # REMOVED frame_type from key
                "width": self.frame_config["width"], "height": self.frame_config["height"],
                "marginX": self.frame_config.get("margin_x", 0), "marginY": self.frame_config.get("margin_y", 0),
                "frames": frames_for_card_obj,
                "artSource": art_crop_url, "artX": art_x, "artY": art_y, "artZoom": art_zoom, "artRotate": art_rotate,
                "setSymbolSource": set_symbol_source_url, "setSymbolX":set_symbol_x, "setSymbolY": set_symbol_y, "setSymbolZoom": set_symbol_zoom,
                "watermarkSource": f"{ccProto}://{ccHost}:{ccPort}/{self.frame_config['watermark_source']}",
                "watermarkX": self.frame_config["watermark_x"], "watermarkY": self.frame_config["watermark_y"], "watermarkZoom": self.frame_config["watermark_zoom"], 
                "watermarkLeft": self.frame_config["watermark_left"], "watermarkRight": self.frame_config["watermark_right"], "watermarkOpacity": self.frame_config["watermark_opacity"],
                "version": self.frame_config.get("version_string", self.frame_type), "showsFlavorBar": self.frame_config.get("shows_flavor_bar", False), "manaSymbols": mana_symbols,
                "infoYear": DEFAULT_INFO_YEAR, "margins": self.frame_config.get("margins", False),
                "bottomInfoTranslate": self.frame_config.get("bottomInfoTranslate", {"x": 0, "y": 0}), "bottomInfoRotate": self.frame_config.get("bottomInfoRotate", 0),
                "bottomInfoZoom": self.frame_config.get("bottomInfoZoom", 1), "bottomInfoColor": self.frame_config.get("bottomInfoColor", "white"),
                "onload": self.frame_config.get("onload", None), "hideBottomInfoBorder": self.frame_config.get("hideBottomInfoBorder", False),
                "bottomInfo": self.frame_config.get("bottom_info", {}), "artBounds": self.frame_config.get("art_bounds", {}),
                "setSymbolBounds": self.frame_config.get("set_symbol_bounds", {}), "watermarkBounds": self.frame_config.get("watermark_bounds", {}),
                                "text": { # MODIFIED TEXT POPULATION TO FIX ERROR
                    "mana": {
                        **self.frame_config.get("text", {}).get("mana", {}),
                        "text": card_data.get('mana_cost', '')
                    },
                    "title": {
                        **self.frame_config.get("text", {}).get("title", {}),
                        "text": card_data.get('name', card_name)
                    },
                    "type": {
                        **self.frame_config.get("text", {}).get("type", {}),
                        "text": card_data.get('type_line', 'Instant')
                    },
                    "rules": { 
                        **self.frame_config.get("text", {}).get("rules", {}),
                        "text": final_rules_text 
                    },
                    "pt": { # MODIFIED TO USE pt_text_final
                        **self.frame_config.get("text", {}).get("pt", {}),
                        "text": pt_text_final 
                    }
                },
                "infoNumber": DEFAULT_INFO_NUMBER, "infoRarity": rarity_code_for_symbol.upper() if rarity_code_for_symbol else DEFAULT_INFO_RARITY, 
                "infoSet": set_code_from_scryfall.upper(), "infoLanguage": DEFAULT_INFO_LANGUAGE, "infoArtist": artist_name, "infoNote": DEFAULT_INFO_NOTE,
                "noCorners": self.frame_config.get("noCorners", True)}}
        if self.frame_type == "8th": card_obj["data"].update({"serialNumber": "", "serialTotal": "", "serialX": "", "serialY": "", "serialScale": ""})
        return card_obj