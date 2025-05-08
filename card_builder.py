# --- file: card_builder.py ---
"""
Module for building card data structure from Scryfall data
"""
import logging
from typing import Dict, List, Optional, Union, Tuple
import io # For handling image bytes
import re # For parsing numbers from strings

import requests # For fetching image
from PIL import Image # For getting image dimensions
from lxml import etree # For SVG set symbols

from config import (
    ccProto, ccHost, ccPort,
    DEFAULT_INFO_YEAR, DEFAULT_INFO_RARITY, DEFAULT_INFO_SET,
    DEFAULT_INFO_LANGUAGE, DEFAULT_INFO_ARTIST, DEFAULT_INFO_NOTE, DEFAULT_INFO_NUMBER
)
from color_mapping import COLOR_CODE_MAP, RARITY_MAP

logger = logging.getLogger(__name__)

class CardBuilder:
    """Class for building card data from Scryfall data"""
    
    def __init__(self, frame_type: str, frame_config: Dict, frame_set: str = "regular", legendary_crowns: bool = False, auto_fit_art: bool = False, set_symbol_override: Optional[str] = None, auto_fit_set_symbol: bool = False):
        """Initialize the CardBuilder with the frame type and configuration.
        
        Args:
            frame_type: "seventh" or "8th"
            frame_config: The frame configuration dictionary
            frame_set: The frame set to use (only for seventh edition)
        """
        self.frame_type = frame_type
        self.frame_config = frame_config
        self.frame_set = frame_set
        self.legendary_crowns = legendary_crowns
        self.auto_fit_art = auto_fit_art
        self.set_symbol_override = set_symbol_override
        self.auto_fit_set_symbol = auto_fit_set_symbol

# --- Add this method inside the CardBuilder class in card_builder.py ---

    def _calculate_auto_fit_set_symbol_params(self, set_symbol_url: str) -> Optional[Dict[str, float]]:
        """
        Calculates setSymbolX, setSymbolY, and setSymbolZoom to make the set symbol
        fit and center within its defined bounds on the card.
        """
        if not set_symbol_url:
            logger.warning("No set symbol URL provided for auto-fit calculation.")
            return None

        try:
            # Fetch SVG content
            response = requests.get(set_symbol_url, timeout=10)
            response.raise_for_status()
            svg_bytes = response.content

            # Get SVG intrinsic dimensions
            svg_dims = self._get_svg_dimensions(svg_bytes)
            if not svg_dims or svg_dims["width"] <= 0 or svg_dims["height"] <= 0:
                logger.warning(f"Could not get valid dimensions for SVG: {set_symbol_url}")
                return None

            svg_intrinsic_width = svg_dims["width"]
            svg_intrinsic_height = svg_dims["height"]

            # Get card and set symbol box dimensions from frame config
            card_total_width = self.frame_config.get("width")
            card_total_height = self.frame_config.get("height")
            symbol_bounds_config = self.frame_config.get("set_symbol_bounds")

            if not all([card_total_width, card_total_height, symbol_bounds_config]):
                logger.warning("Frame config missing width, height, or set_symbol_bounds for auto-fit.")
                return None

            # Relative bounds of the set symbol box (0-1)
            s_bound_rel_x = symbol_bounds_config.get("x", 0.0)
            s_bound_rel_y = symbol_bounds_config.get("y", 0.0)
            s_bound_rel_width = symbol_bounds_config.get("width", 0.1) # Default to a small width if not set
            s_bound_rel_height = symbol_bounds_config.get("height", 0.1) # Default to a small height

            # Absolute pixel dimensions of the target set symbol box on the card
            target_abs_symbol_box_width = s_bound_rel_width * card_total_width
            target_abs_symbol_box_height = s_bound_rel_height * card_total_height

            if target_abs_symbol_box_width <=0 or target_abs_symbol_box_height <=0:
                logger.warning(f"Set symbol bounds have zero or negative dimensions in config. W: {target_abs_symbol_box_width}, H: {target_abs_symbol_box_height}")
                return None

            # Calculate scale to make SVG fit *inside* the box, preserving aspect ratio
            scale_x_factor = target_abs_symbol_box_width / svg_intrinsic_width
            scale_y_factor = target_abs_symbol_box_height / svg_intrinsic_height

            calculated_zoom = min(scale_x_factor, scale_y_factor)

            # If calculated zoom is extremely small or zero, something is wrong (e.g. massive SVG or tiny bounds)
            if calculated_zoom <= 1e-6: # Threshold for too small zoom
                logger.warning(f"Calculated set symbol zoom is near zero ({calculated_zoom:.2e}) for {set_symbol_url}. SVG: {svg_intrinsic_width}x{svg_intrinsic_height}, Bounds: {target_abs_symbol_box_width:.1f}x{target_abs_symbol_box_height:.1f}. Using default zoom from config.")
                # Fallback to default zoom to avoid invisible symbol, but X/Y might still be off.
                # A better fallback might be to not change X,Y,Zoom at all.
                # For now, we return None to indicate failure and use full defaults.
                return None


            # Calculate new X and Y to center the scaled SVG within the symbol_bounds_config
            # scaled_symbol_abs_width/height are the dimensions of the SVG *after* applying calculated_zoom
            # but still in the SVG's original unit system if we think of zoom as unitless.
            # More directly, these are the dimensions the symbol will take up on the card in pixels.
            scaled_symbol_on_card_width_px = svg_intrinsic_width * calculated_zoom
            scaled_symbol_on_card_height_px = svg_intrinsic_height * calculated_zoom

            # Calculate offsets to center the symbol within its box, then convert to relative (0-1)
            # new_x = box_origin_x + (box_width - symbol_width_after_zoom) / 2
            calculated_set_symbol_x_relative = s_bound_rel_x + \
                (target_abs_symbol_box_width - scaled_symbol_on_card_width_px) / 2 / card_total_width

            calculated_set_symbol_y_relative = s_bound_rel_y + \
                (target_abs_symbol_box_height - scaled_symbol_on_card_height_px) / 2 / card_total_height

            logger.info(f"Auto-fit for set symbol {set_symbol_url}: Zoom={calculated_zoom:.4f}, X={calculated_set_symbol_x_relative:.4f}, Y={calculated_set_symbol_y_relative:.4f}")

            return {
                "setSymbolX": calculated_set_symbol_x_relative,
                "setSymbolY": calculated_set_symbol_y_relative,
                "setSymbolZoom": calculated_zoom
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching set symbol SVG from {set_symbol_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during auto-fit set symbol calculation for {set_symbol_url}: {e}")
            return None

# --- Add this method inside the CardBuilder class in card_builder.py ---

    def _get_svg_dimensions(self, svg_content_bytes: bytes) -> Optional[Dict[str, float]]:
        """
        Parses SVG content to get its intrinsic width and height.
        Prioritizes viewBox, then width/height attributes if absolute.
        """
        if not svg_content_bytes:
            return None
        try:
            # Prevent XML External Entity (XXE) attacks by using a non-network-accessing parser
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            svg_root = etree.fromstring(svg_content_bytes, parser=parser)
            
            # Ensure we are dealing with an SVG root element
            if not svg_root.tag.endswith('svg'): # Handles namespace like {http://www.w3.org/2000/svg}svg
                logger.warning("Parsed XML root is not an SVG element.")
                return None

            viewbox_str = svg_root.get("viewBox")
            width_str = svg_root.get("width")
            height_str = svg_root.get("height")

            intrinsic_width = None
            intrinsic_height = None

            if viewbox_str:
                try:
                    # viewBox is "min-x min-y width height"
                    parts = [float(p) for p in re.split(r'[,\s]+', viewbox_str.strip())]
                    if len(parts) == 4:
                        intrinsic_width = parts[2]
                        intrinsic_height = parts[3]
                        logger.debug(f"SVG viewBox parsed: w={intrinsic_width}, h={intrinsic_height}")
                except ValueError:
                    logger.warning(f"Could not parse SVG viewBox string: {viewbox_str}")
            
            # If viewBox didn't yield dimensions, try width/height attributes
            # Only use them if they are absolute values (don't end in '%')
            if intrinsic_width is None and width_str and not width_str.endswith('%'):
                try:
                    # Remove "px" or other units if present, then convert to float
                    intrinsic_width = float(re.sub(r'[^\d\.]', '', width_str))
                except ValueError:
                    logger.warning(f"Could not parse SVG width attribute: {width_str}")
            
            if intrinsic_height is None and height_str and not height_str.endswith('%'):
                try:
                    intrinsic_height = float(re.sub(r'[^\d\.]', '', height_str))
                except ValueError:
                    logger.warning(f"Could not parse SVG height attribute: {height_str}")

            if intrinsic_width is not None and intrinsic_height is not None and intrinsic_width > 0 and intrinsic_height > 0:
                return {"width": intrinsic_width, "height": intrinsic_height}
            else:
                logger.warning(f"Could not determine valid intrinsic dimensions for SVG. W: {width_str}, H: {height_str}, VB: {viewbox_str}")
                return None

        except etree.XMLSyntaxError as e:
            logger.error(f"Error parsing SVG XML: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting SVG dimensions: {e}")
            return None

# --- Inside the CardBuilder class in card_builder.py ---

    def _calculate_auto_fit_art_params(self, art_url: str) -> Optional[Dict[str, float]]:
        """
        Calculates artX, artY, and artZoom to make art cover the art box.
        Returns a dict with {'artX', 'artY', 'artZoom'} or None if an error occurs.
        """
        if not art_url:
            logger.warning("No art URL provided for auto-fit calculation.")
            return None

        try:
            # Fetch image data
            response = requests.get(art_url, timeout=10)
            response.raise_for_status() # Raise an exception for bad status codes
            
            # Get image dimensions using Pillow
            img = Image.open(io.BytesIO(response.content))
            art_natural_width, art_natural_height = img.width, img.height
            img.close()

            if art_natural_width == 0 or art_natural_height == 0:
                logger.warning(f"Art image from {art_url} has zero dimensions.")
                return None

            # Get card and art box dimensions from frame config
            # These are the absolute pixel dimensions of the card output
            card_total_width = self.frame_config.get("width")
            card_total_height = self.frame_config.get("height")
            
            # These are relative (0-1) bounds of the art box within the card
            art_bounds_config = self.frame_config.get("art_bounds")

            if not all([card_total_width, card_total_height, art_bounds_config]):
                logger.warning("Frame configuration missing width, height, or art_bounds for auto-fit.")
                return None

            art_box_relative_x = art_bounds_config.get("x", 0)
            art_box_relative_y = art_bounds_config.get("y", 0)
            art_box_relative_width = art_bounds_config.get("width", 1)
            art_box_relative_height = art_bounds_config.get("height", 1)

            # Absolute dimensions of the target art box on the card
            target_abs_art_box_width = art_box_relative_width * card_total_width
            target_abs_art_box_height = art_box_relative_height * card_total_height
            
            # Calculate scale to make art cover the box
            scale_x = target_abs_art_box_width / art_natural_width
            scale_y = target_abs_art_box_height / art_natural_height
            
            calculated_zoom = max(scale_x, scale_y)

            # Calculate new artX and artY (relative to card dimensions 0-1)
            # These formulas match CardConjurer's autoFitArt()
            # (target_abs_art_box_width - art_natural_width * calculated_zoom) is the horizontal "empty space" (could be negative if cropping)
            # We divide by 2 to center it, then divide by card_total_width to make it relative again.
            calculated_art_x = art_box_relative_x + \
                               (target_abs_art_box_width - art_natural_width * calculated_zoom) / 2 / card_total_width
            
            calculated_art_y = art_box_relative_y + \
                               (target_abs_art_box_height - art_natural_height * calculated_zoom) / 2 / card_total_height
            
            logger.info(f"Auto-fit for {art_url}: Zoom={calculated_zoom:.4f}, X={calculated_art_x:.4f}, Y={calculated_art_y:.4f}")
            return {
                "artX": calculated_art_x,
                "artY": calculated_art_y,
                "artZoom": calculated_zoom
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching art image from {art_url}: {e}")
            return None
        except IOError as e: # Pillow error
            logger.error(f"Error processing art image from {art_url} with Pillow: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during auto-fit art calculation for {art_url}: {e}")
            return None
    
    def build_frame_path(self, color_code: str) -> str:
        """Build the frame path based on the frame type and color code.
        
        Args:
            color_code: The color code (e.g., "w" for white)
            
        Returns:
            The path to the frame image
        """
        if self.frame_config["uses_frame_set"]:
            return self.frame_config["frame_path_format"].format(
                frame=self.frame_type,
                frame_set=self.frame_set,
                color_code=color_code
            )
        else:
            return self.frame_config["frame_path_format"].format(
                frame=self.frame_type,
                color_code=color_code
            )
    
    def build_mask_path(self, mask_name: str) -> str:
        """Build the mask path based on the frame type and mask name.
        
        Args:
            mask_name: The name of the mask (e.g., "pinline", "border")
            
        Returns:
            The path to the mask image
        """
        if self.frame_type == "8th":
            # 8th edition has a special case - only border is SVG
            ext = ".svg" if mask_name == "border" else ".png"
            return f"/img/frames/8th/{mask_name}{ext}"
        elif self.frame_config["uses_frame_set"]:
            return self.frame_config["mask_path_format"].format(
                frame=self.frame_type,
                frame_set=self.frame_set,
                mask_name=mask_name
            )
        else:
            return self.frame_config["mask_path_format"].format(
                frame=self.frame_type,
                mask_name=mask_name
            )
    
    def build_land_frame_path(self, color_code: str) -> str:
        """Build the land-specific frame path.
        
        Args:
            color_code: The color code (e.g., "w" for white)
            
        Returns:
            The path to the land frame image
        """
        if self.frame_type == "8th":
            # For 8th edition, append 'l' to color code for land frames
            return f"/img/frames/8th/{color_code}l.png"
        elif self.frame_config["uses_frame_set"]:
            base_path = f"/img/frames/{self.frame_type}/{self.frame_set}/"
            return base_path + self.frame_config["land_color_format"].format(color_code=color_code)
        else:
            base_path = f"/img/frames/{self.frame_type}/"
            return base_path + self.frame_config["land_color_format"].format(color_code=color_code)
    
    def build_pt_frame_path(self, color_code: str) -> Optional[str]:
        """Build the P/T box frame path for 8th edition.
        
        Args:
            color_code: The color code (e.g., "w" for white)
            
        Returns:
            The path to the P/T box frame image, or None if not applicable
        """
        if "pt_path_format" in self.frame_config:
            return self.frame_config["pt_path_format"].format(
                frame=self.frame_type,
                color_code=color_code
            )
        return None

# --- In card_builder.py, replace the existing build_m15_frames method ---

    def build_m15_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:
        generated_frames = []
        card_name_for_logging = card_data.get('name', 'Unknown Card')

        # --- 1. Legendary Crown (if applicable) ---
        is_legendary = 'Legendary' in card_data.get('type_line', '')
        if self.legendary_crowns and is_legendary:
            logger.debug(f"Building legendary crown for M15 frame on '{card_name_for_logging}'.")
            
            primary_crown_color_code = None
            secondary_crown_color_code = None 

            if isinstance(color_info, dict) and color_info.get('is_gold') and color_info.get('component_colors'):
                if len(color_info['component_colors']) >= 1:
                    primary_crown_color_code = color_info['component_colors'][0]['code']
                if len(color_info['component_colors']) >= 2:
                    secondary_crown_color_code = color_info['component_colors'][1]['code']
            elif isinstance(color_info, dict) and color_info.get('code'): 
                primary_crown_color_code = color_info['code']
            # Lands that are legendary are an edge case for crowns; current logic assumes dict color_info for crowns

            if primary_crown_color_code: # <<<< IF THIS IS LINE 229 (or similar)
                # vvvv ALL OF THIS BLOCK NEEDS TO BE INDENTED vvvv
                crown_path_format = self.frame_config.get("legend_crown_path_format")
                crown_bounds = self.frame_config.get("legend_crown_bounds")
                cover_bounds = self.frame_config.get("legend_crown_cover_bounds")

                if crown_path_format and crown_bounds and cover_bounds:
                    comp_colors = [] # Default to empty list
                    if isinstance(color_info, dict) and color_info.get('is_gold'):
                        comp_colors = color_info.get('component_colors', [])

                    if secondary_crown_color_code:
                        sec_name = comp_colors[1]['name'] if len(comp_colors) > 1 and 'name' in comp_colors[1] else "Secondary"
                        generated_frames.append({
                            "name": f"{sec_name} Legend Crown",
                            "src": crown_path_format.format(color_code=secondary_crown_color_code),
                            "masks": [{"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}],
                            "bounds": crown_bounds
                        })
                    
                    prim_name = "Legend" 
                    if isinstance(color_info, dict):
                        if color_info.get('is_gold') and len(comp_colors) > 0 and 'name' in comp_colors[0]:
                             prim_name = comp_colors[0]['name']
                        elif not color_info.get('is_gold') and color_info.get('name'): # Monocolor/Artifact
                             prim_name = color_info.get('name')
                    
                    generated_frames.append({
                        "name": f"{prim_name} Legend Crown", 
                        "src": crown_path_format.format(color_code=primary_crown_color_code),
                        "masks": [],
                        "bounds": crown_bounds
                    })
                    
                    generated_frames.append({
                        "name": "Legend Crown Border Cover",
                        "src": "/img/black.png", 
                        "masks": [],
                        "bounds": cover_bounds
                    })
                else:
                    logger.warning(f"M15 legendary crown configuration missing for '{card_name_for_logging}'.")
                # ^^^^ END OF INDENTED BLOCK FOR if primary_crown_color_code ^^^^
            elif is_legendary: 
                logger.warning(f"Could not determine color for M15 legendary crown on '{card_name_for_logging}'. color_info: {color_info}")

        # --- 2. Power/Toughness Box (if applicable) ---
        if 'power' in card_data and 'toughness' in card_data:
            pt_code = None
            pt_name_prefix = "Unknown"

            if isinstance(color_info, dict) and color_info.get('is_gold'):
                pt_code = COLOR_CODE_MAP.get('M', {}).get('code', 'm') 
                pt_name_prefix = COLOR_CODE_MAP.get('M', {}).get('name', "Multicolored")
            elif isinstance(color_info, dict) and color_info.get('code'):
                pt_code = color_info['code'] 
                pt_name_prefix = color_info['name']
            
            if pt_code:
                pt_path_format = self.frame_config.get("pt_path_format")
                pt_bounds = self.frame_config.get("pt_bounds")
                if pt_path_format and pt_bounds:
                    generated_frames.append({
                        "name": f"{pt_name_prefix} Power/Toughness",
                        "src": pt_path_format.format(color_code=pt_code.upper()),
                        "masks": [],
                        "bounds": pt_bounds
                    })
                else:
                    logger.warning(f"M15 P/T box configuration missing for '{card_name_for_logging}'.")
            else:
                 logger.warning(f"Could not determine P/T color code for M15 on '{card_name_for_logging}'. color_info: {color_info}")
        
        # --- 3. Main Card Frame Layers ---
        main_frame_layers = []
        base_frame_path = self.frame_config.get("frame_path_format")
        mask_path_format = self.frame_config.get("mask_path_format")
        
        main_frame_mask_src_name = self.frame_config.get("frame_mask_name_for_main_frame_layer", "Frame.png") 
        main_border_mask_src_name = self.frame_config.get("border_mask_name_for_main_frame_layer", "Border.png")

        # Construct full paths if names are relative to the base_frame_path directory
        frame_base_dir = ""
        if base_frame_path and '/' in base_frame_path:
             frame_base_dir = base_frame_path.rsplit('/', 1)[0]
        
        main_frame_mask_src = f"{frame_base_dir}/{main_frame_mask_src_name}" if frame_base_dir and not main_frame_mask_src_name.startswith("/img/") else main_frame_mask_src_name
        main_border_mask_src = f"{frame_base_dir}/{main_border_mask_src_name}" if frame_base_dir and not main_border_mask_src_name.startswith("/img/") else main_border_mask_src_name


        if not base_frame_path or not mask_path_format:
            logger.error(f"M15 frame/mask path configuration missing for '{card_name_for_logging}'.")
            generated_frames.extend(main_frame_layers) 
            return generated_frames

        primary_color_code = None
        primary_color_name = "Unknown"
        secondary_color_code = None 
        secondary_color_name = None
        
        base_multicolor_code = COLOR_CODE_MAP.get('M', {}).get('code', 'm') 
        base_multicolor_name = COLOR_CODE_MAP.get('M', {}).get('name', "Multicolored")

        if isinstance(color_info, dict): 
            if color_info.get('is_gold') and color_info.get('component_colors'):
                components = color_info['component_colors']
                if len(components) >= 1:
                    primary_color_code = components[0]['code']
                    primary_color_name = components[0]['name']
                if len(components) >= 2:
                    secondary_color_code = components[1]['code']
                    secondary_color_name = components[1]['name']
            elif color_info.get('code'): 
                primary_color_code = color_info['code']
                primary_color_name = color_info['name']
        
        elif isinstance(color_info, list): # Lands
            if len(color_info) >= 2: 
                primary_color_code = color_info[1]['code'] 
                primary_color_name = color_info[1]['name']
            if len(color_info) >= 3: 
                secondary_color_code = color_info[2]['code'] 
                secondary_color_name = color_info[2]['name']
            if not primary_color_code and len(color_info) == 1 and 'code' in color_info[0]:
                primary_color_code = color_info[0]['code'] 
                primary_color_name = color_info[0]['name']
        else:
            logger.error(f"Unexpected color_info type for M15 main frame on '{card_name_for_logging}'. color_info: {color_info}")
            # Fall through to primary_color_code check

        if not primary_color_code:
            logger.error(f"Primary color code missing for M15 main frame construction on '{card_name_for_logging}'. color_info: {color_info}")
            generated_frames.extend(main_frame_layers)
            return generated_frames

        # Determine the code/name for Type, Title, Frame, Border on multicolor/dual-land cards
        # For M15 creatures (gold), these use 'M'. For M15 lands, they likely use 'L'.
        ttfb_code = base_multicolor_code # Default to 'm' for (Type,Title,Frame,Border)
        ttfb_name = base_multicolor_name
        if isinstance(color_info, list): # If it's a land
            ttfb_code = COLOR_CODE_MAP.get('L',{}).get('code','l')
            ttfb_name = COLOR_CODE_MAP.get('L',{}).get('name','Land')
        elif isinstance(color_info, dict) and not color_info.get('is_gold'): # Monocolor, Artifact
            ttfb_code = primary_color_code
            ttfb_name = primary_color_name


        if secondary_color_code: # Gold card or dual-color land
            main_frame_layers.append({ "name": f"{secondary_color_name} Frame", "src": base_frame_path.format(color_code=secondary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Pinline"), "name": "Pinline"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]})
            main_frame_layers.append({ "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Pinline"), "name": "Pinline"}]})
            main_frame_layers.append({ "name": f"{ttfb_name} Frame", "src": base_frame_path.format(color_code=ttfb_code), "masks": [{"src": mask_path_format.format(mask_name="Type"), "name": "Type"}]})
            main_frame_layers.append({ "name": f"{ttfb_name} Frame", "src": base_frame_path.format(color_code=ttfb_code), "masks": [{"src": mask_path_format.format(mask_name="Title"), "name": "Title"}]})
            main_frame_layers.append({ "name": f"{secondary_color_name} Frame", "src": base_frame_path.format(color_code=secondary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Rules"), "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]})
            main_frame_layers.append({ "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Rules"), "name": "Rules"}]})
            main_frame_layers.append({ "name": f"{ttfb_name} Frame", "src": base_frame_path.format(color_code=ttfb_code), "masks": [{"src": main_frame_mask_src, "name": "Frame"}]})
            main_frame_layers.append({ "name": f"{ttfb_name} Frame", "src": base_frame_path.format(color_code=ttfb_code), "masks": [{"src": main_border_mask_src, "name": "Border"}]})
        else: # Monocolor, Artifact, Colorless, or single-color Land
            main_frame_layers.extend([
                { "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Pinline"), "name": "Pinline"}]},
                { "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Type"), "name": "Type"}]}, # Note: ttfb_code/name could be used here too
                { "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Title"), "name": "Title"}]},
                { "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": mask_path_format.format(mask_name="Rules"), "name": "Rules"}]},
                { "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": main_frame_mask_src, "name": "Frame"}]},
                { "name": f"{primary_color_name} Frame", "src": base_frame_path.format(color_code=primary_color_code), "masks": [{"src": main_border_mask_src, "name": "Border"}]}
            ])

        generated_frames.extend(main_frame_layers)
        return generated_frames

# --- In card_builder.py, replace the existing build_eighth_edition_frames method ---

    def build_eighth_edition_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:
        """Build frames for 8th edition cards.
        
        Args:
            color_info: The color information for the card (can be Dict or List for lands)
            card_data: The card data from Scryfall
            
        Returns:
            A list of frame objects for CardConjurer
        """
        generated_frames = [] 

        # --- 1. Add Power/Toughness Box if applicable ---
        if 'power' in card_data and 'toughness' in card_data:
            pt_color_code = None
            pt_name_prefix = None

            if isinstance(color_info, dict): # Non-land with P/T
                code = color_info.get('code')
                name = color_info.get('name')

                if code == 'a': # Artifact creature
                    pt_color_code = 'a'
                    pt_name_prefix = "Artifact"
                elif code == 'm': # Gold/Multicolor creature (assuming 'm' for P/T box code)
                    pt_color_code = 'm' # Or 'gld', check your /img/frames/8th/pt/ content
                    pt_name_prefix = "Gold" # Or "Multicolor"
                elif code in ['w', 'u', 'b', 'r', 'g', 'c']: # Single color or colorless
                    pt_color_code = code
                    pt_name_prefix = name
                else:
                    logger.warning(f"Unknown P/T color code '{code}' for card: {card_data.get('name')}. color_info: {color_info}")

            # Note: Lands (isinstance(color_info, list)) typically don't have P/T.
            # If you have land creatures with P/T (e.g., Dryad Arbor) and color_info is a list for them,
            # this P/T logic would need to be adapted to extract the correct color from the list.

            if pt_color_code and pt_name_prefix:
                pt_path = self.build_pt_frame_path(pt_color_code) # Uses self.frame_config["pt_path_format"]
                if pt_path:
                    generated_frames.append({
                        "name": f"{pt_name_prefix} Power/Toughness",
                        "src": pt_path,
                        "masks": [],
                        "bounds": { # These bounds are from your working examples
                            "height": 0.0839,
                            "width": 0.2147,
                            "x": 0.7227,
                            "y": 0.8796
                        }
                    })
                else:
                    logger.warning(f"Could not build P/T path for {pt_color_code} for card: {card_data.get('name')}")
            elif 'power' in card_data: # Only log if P/T exists but we couldn't determine color
                 logger.warning(f"Could not determine P/T color information for card: {card_data.get('name')}. color_info: {color_info}")


        # --- 2. Add Main Card Frame Layers ---
        main_frame_layers = [] # Temporary list for the main layers

        if isinstance(color_info, list): # LANDS
            # This is your existing logic for lands, adapted to use 'main_frame_layers.extend'
            land_base_frame_info = color_info[0]  # e.g., {'code': 'l', 'name': 'Land'}
            
            # Dual land with at least 2 mana symbol colors (e.g. Badlands: [{'code':'l',...}, {'code':'b',...}, {'code':'r',...}])
            if len(color_info) > 2:
                first_mana_color = color_info[1]
                second_mana_color = color_info[2]
                
                main_frame_layers.extend([
                    {
                        "name": f"{second_mana_color['name']} Land Frame",
                        "src": f"/img/frames/8th/{second_mana_color['code']}l.png", # Assuming 'l' suffix for colored land parts
                        "masks": [
                            {"src": "/img/frames/8th/pinline.png", "name": "Pinline"},
                            {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}
                        ]
                    },
                    {
                        "name": f"{first_mana_color['name']} Land Frame",
                        "src": f"/img/frames/8th/{first_mana_color['code']}l.png",
                        "masks": [
                            {"src": "/img/frames/8th/pinline.png", "name": "Pinline"}
                        ]
                    },
                    {
                        "name": "Land Frame", # Generic parts
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png", # e.g., /img/frames/8th/l.png
                        "masks": [{"src": "/img/frames/8th/type.png", "name": "Type"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/title.png", "name": "Title"}]
                    },
                    {
                        "name": f"{second_mana_color['name']} Land Frame",
                        "src": f"/img/frames/8th/{second_mana_color['code']}l.png",
                        "masks": [
                            {"src": "/img/frames/8th/rules.png", "name": "Rules"},
                            {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}
                        ]
                    },
                    {
                        "name": f"{first_mana_color['name']} Land Frame",
                        "src": f"/img/frames/8th/{first_mana_color['code']}l.png",
                        "masks": [
                            {"src": "/img/frames/8th/rules.png", "name": "Rules"}
                        ]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/frame.png", "name": "Frame"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/border.svg", "name": "Border"}]
                    }
                ])
            # Single-mana-color land (e.g. Snow-Covered Forest: [{'code':'l',...}, {'code':'g',...}])
            elif len(color_info) > 1:
                mana_color = color_info[1]
                main_frame_layers.extend([
                    {
                        "name": f"{mana_color['name']} Land Frame",
                        "src": f"/img/frames/8th/{mana_color['code']}l.png",
                        "masks": [{"src": "/img/frames/8th/pinline.png", "name": "Pinline"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/type.png", "name": "Type"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/title.png", "name": "Title"}]
                    },
                    {
                        "name": f"{mana_color['name']} Land Frame",
                        "src": f"/img/frames/8th/{mana_color['code']}l.png",
                        "masks": [{"src": "/img/frames/8th/rules.png", "name": "Rules"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/frame.png", "name": "Frame"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/border.svg", "name": "Border"}]
                    }
                ])
            # Basic land with no specific mana color identified beyond being a land (e.g. Wastes: [{'code':'l',...}])
            else:
                main_frame_layers.extend([
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/pinline.png", "name": "Pinline"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/type.png", "name": "Type"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/title.png", "name": "Title"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/rules.png", "name": "Rules"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/frame.png", "name": "Frame"}]
                    },
                    {
                        "name": "Land Frame",
                        "src": f"/img/frames/8th/{land_base_frame_info['code']}.png",
                        "masks": [{"src": "/img/frames/8th/border.svg", "name": "Border"}]
                    }
                ])
        
        elif isinstance(color_info, dict): # Regular NON-LAND card (Creature, Artifact, Enchantment, etc.)
            main_frame_color_code = color_info.get('code')
            main_frame_color_name = color_info.get('name')

            if main_frame_color_code and main_frame_color_name:
                # These are the standard 6 layers for a non-land 8th ed card
                main_frame_layers.extend([
                    {
                        "name": f"{main_frame_color_name} Frame",
                        "src": f"/img/frames/8th/{main_frame_color_code}.png",
                        "masks": [{"src": "/img/frames/8th/pinline.png", "name": "Pinline"}]
                    },
                    {
                        "name": f"{main_frame_color_name} Frame",
                        "src": f"/img/frames/8th/{main_frame_color_code}.png",
                        "masks": [{"src": "/img/frames/8th/type.png", "name": "Type"}]
                    },
                    {
                        "name": f"{main_frame_color_name} Frame",
                        "src": f"/img/frames/8th/{main_frame_color_code}.png",
                        "masks": [{"src": "/img/frames/8th/title.png", "name": "Title"}]
                    },
                    {
                        "name": f"{main_frame_color_name} Frame",
                        "src": f"/img/frames/8th/{main_frame_color_code}.png",
                        "masks": [{"src": "/img/frames/8th/rules.png", "name": "Rules"}]
                    },
                    {
                        "name": f"{main_frame_color_name} Frame",
                        "src": f"/img/frames/8th/{main_frame_color_code}.png",
                        "masks": [{"src": "/img/frames/8th/frame.png", "name": "Frame"}]
                    },
                    {
                        "name": f"{main_frame_color_name} Frame",
                        "src": f"/img/frames/8th/{main_frame_color_code}.png",
                        "masks": [{"src": "/img/frames/8th/border.svg", "name": "Border"}]
                    }
                ])
            else:
                logger.error(f"Could not determine main frame color for non-land card: {card_data.get('name')}. color_info: {color_info}")
        else:
            logger.error(f"Unexpected color_info type for card {card_data.get('name')}: {type(color_info)}. Value: {color_info}")

        generated_frames.extend(main_frame_layers) # Add the main layers to the P/T (if any)
        
        return generated_frames
    
    def build_seventh_edition_frames(self, color_info, card_data: Dict) -> List[Dict]:
        """Build frames for 7th edition cards.
        
        Args:
            color_info: The color information for the card
            card_data: The card data from Scryfall
            
        Returns:
            A list of frame objects for CardConjurer
        """
        frames = []
        
        # Handle dual lands vs single color cards
        if isinstance(color_info, list):
            land_frame = color_info[0]  # Base land frame
            
            # Dual land with at least 2 colors
            if len(color_info) > 2:
                first_color = color_info[1]
                second_color = color_info[2]
                
                # Add the base pinline frame
                frames.append({
                    "name": f"{land_frame['name']} Frame",
                    "src": self.build_frame_path(land_frame['code']),
                    "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]
                })
                
                # Add the colored rule frames
                frames.append({
                    "name": f"{second_color['name']} Land Frame",
                    "src": self.build_land_frame_path(second_color['code']),
                    "masks": [
                        {"src": self.build_mask_path("rules"), "name": "Rules"},
                        {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}
                    ]
                })
                
                frames.append({
                    "name": f"{first_color['name']} Land Frame",
                    "src": self.build_land_frame_path(first_color['code']),
                    "masks": [
                        {"src": self.build_mask_path("rules"), "name": "Rules"}
                    ]
                })
                
                # Add the remaining frames
                frames.extend([
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("trim"), "name": "Textbox Pinline"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]
                    }
                ])
            # Single-color land
            elif len(color_info) > 1:
                color = color_info[1]
                
                # Add the base pinline frame
                frames.append({
                    "name": f"{land_frame['name']} Frame",
                    "src": self.build_frame_path(land_frame['code']),
                    "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]
                })
                
                # Add the colored rule frame
                frames.append({
                    "name": f"{color['name']} Land Frame",
                    "src": self.build_land_frame_path(color['code']),
                    "masks": [
                        {"src": self.build_mask_path("rules"), "name": "Rules"}
                    ]
                })
                
                # Add the remaining frames
                frames.extend([
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("trim"), "name": "Textbox Pinline"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]
                    }
                ])
            # Basic land with no color
            else:
                frames = [
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("trim"), "name": "Textbox Pinline"}]
                    },
                    {
                        "name": f"{land_frame['name']} Frame",
                        "src": self.build_frame_path(land_frame['code']),
                        "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]
                    }
                ]
        # Regular non-land card
        else:
            color_code = color_info['code']
            color_name = color_info['name']
            
            frames = [
                {
                    "name": f"{color_name} Frame",
                    "src": self.build_frame_path(color_code),
                    "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]
                },
                {
                    "name": f"{color_name} Frame",
                    "src": self.build_frame_path(color_code),
                    "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]
                },
                {
                    "name": f"{color_name} Frame",
                    "src": self.build_frame_path(color_code),
                    "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]
                },
                {
                    "name": f"{color_name} Frame",
                    "src": self.build_frame_path(color_code),
                    "masks": [{"src": self.build_mask_path("trim"), "name": "Textbox Pinline"}]
                },
                {
                    "name": f"{color_name} Frame",
                    "src": self.build_frame_path(color_code),
                    "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]
                }
            ]
        
        return frames
    
    def build_card_data(self, card_name: str, card_data: Dict, color_info) -> Dict:
        """Build complete card data object for CardConjurer.
        
        Args:
            card_name: The name of the card
            card_data: The card data from Scryfall
            color_info: The color information for the card
            
        Returns:
            A dictionary containing the complete card data for CardConjurer
        """
        # Determine frames based on frame type
        frames_for_card_obj = [] # Initialize
        if self.frame_type == "8th":
            frames_for_card_obj = self.build_eighth_edition_frames(color_info, card_data)
        elif self.frame_type == "m15": # <<< ADD THIS CASE
            frames_for_card_obj = self.build_m15_frames(color_info, card_data)
        else: # Default to seventh
            frames_for_card_obj = self.build_seventh_edition_frames(color_info, card_data)
        
        # Initialize mana symbol scripts
        mana_symbols = []
        if isinstance(color_info, list):
            # Add mana symbol scripts for lands
            mana_symbols = ["/js/frames/manaSymbolsFuture.js", "/js/frames/manaSymbolsOld.js"]
        
        # Get color code for main object
        if isinstance(color_info, list):
            color_code = color_info[0]['code']  # Base frame (land)
            color_name = color_info[0]['name']
        else:
            color_code = color_info['code']
            color_name = color_info['name']
        
        # Get set and rarity information FROM SCRYFALL for infoSet
        set_code_from_scryfall = card_data.get('set', DEFAULT_INFO_SET)

        rarity_from_scryfall = card_data.get('rarity', 'c') # This is the Scryfall rarity (c, u, r, m, s)
        # Map Scryfall rarity to CardConjurer rarity code (e.g., 'c' -> 'C', 'mythic' -> 'M')
        # This rarity_code_for_symbol will be used for the set symbol image.
        rarity_code_for_symbol = RARITY_MAP.get(rarity_from_scryfall, rarity_from_scryfall) # Fallback to original if not in map

        # Determine which set code to use for the setSymbolSource URL
        if self.set_symbol_override:
            # User provided an override, use that (make it lowercase for URL)
            set_code_for_symbol_url = self.set_symbol_override.lower()
            logger.info(f"Using overridden set symbol code for '{card_name}': {set_code_for_symbol_url}")
        else:
            # No override, use the set code from Scryfall (lowercase for URL)
            set_code_for_symbol_url = set_code_from_scryfall.lower()
        
        # Get artist name
        artist_name = card_data.get('artist', DEFAULT_INFO_ARTIST)
        
        # Get art crop URL
        art_crop_url = ""
        if 'image_uris' in card_data and 'art_crop' in card_data['image_uris']:
            art_crop_url = card_data['image_uris']['art_crop']
        elif 'card_faces' in card_data and card_data['card_faces']:
            for face in card_data['card_faces']:
                if 'image_uris' in face and 'art_crop' in face['image_uris']:
                    art_crop_url = face['image_uris']['art_crop']
                    break

        # Default art parameters from frame config
        art_x = self.frame_config.get("art_x", 0.0)
        art_y = self.frame_config.get("art_y", 0.0)
        art_zoom = self.frame_config.get("art_zoom", 1.0)
        art_rotate = self.frame_config.get("art_rotate", "0") # Keep existing rotate

        # If auto_fit_art is enabled, try to calculate new parameters
        if self.auto_fit_art and art_crop_url:
            logger.info(f"Attempting auto-fit art for {card_name} using URL: {art_crop_url}")
            auto_fit_params = self._calculate_auto_fit_art_params(art_crop_url)
            if auto_fit_params:
                art_x = auto_fit_params["artX"]
                art_y = auto_fit_params["artY"]
                art_zoom = auto_fit_params["artZoom"]
            else:
                logger.warning(f"Auto-fit art failed for {card_name}, using default art parameters from frame config.")

        # Set Symbol Logic
        set_code = card_data.get('set', 'lea').lower() # Ensure lowercase for consistency
        rarity_code = card_data.get('rarity', 'c').lower()
        if rarity_code in RARITY_MAP: # Assuming RARITY_MAP maps to rarity letters if needed
            rarity_code = RARITY_MAP[rarity_code]
        
        set_symbol_source_url = f"{ccProto}://{ccHost}:{ccPort}/img/setSymbols/official/{set_code}-{rarity_code}.svg"

        # Default set symbol parameters from frame config
        default_ss_x = self.frame_config.get("set_symbol_x", 0.0)
        default_ss_y = self.frame_config.get("set_symbol_y", 0.0)
        default_ss_zoom = self.frame_config.get("set_symbol_zoom", 0.1)
        logger.debug(f"Card: {card_name}, Frame: {self.frame_type}, Default Set Symbol Params: X={default_ss_x:.4f}, Y={default_ss_y:.4f}, Zoom={default_ss_zoom:.4f}")

        set_symbol_x = default_ss_x
        set_symbol_y = default_ss_y
        set_symbol_zoom = default_ss_zoom

        if self.auto_fit_set_symbol and set_symbol_source_url:
            logger.info(f"Attempting auto-fit set symbol for {card_name} ({set_code}-{rarity_code}) using URL: {set_symbol_source_url}")
            auto_fit_symbol_params = self._calculate_auto_fit_set_symbol_params(set_symbol_source_url)

            if auto_fit_symbol_params:
                logger.info(f"Card: {card_name}, Auto-fit successful. Applying calculated params: {auto_fit_symbol_params}")
                set_symbol_x = auto_fit_symbol_params["setSymbolX"]
                set_symbol_y = auto_fit_symbol_params["setSymbolY"]
                set_symbol_zoom = auto_fit_symbol_params["setSymbolZoom"]
            else:
                logger.warning(f"Card: {card_name}, Auto-fit set symbol FAILED or returned None. Using default parameters.")
        else:
            logger.debug(f"Card: {card_name}, Auto-fit set symbol NOT ATTEMPTED (flag off or no URL). Using default parameters.")

        logger.info(f"Card: {card_name}, FINAL Set Symbol Params for JSON: X={set_symbol_x:.4f}, Y={set_symbol_y:.4f}, Zoom={set_symbol_zoom:.4f}")
        
        # Build the card data object
        card_obj = {
            "key": card_name,
            "data": {
                "width": self.frame_config["width"],
                "height": self.frame_config["height"],
                "marginX": self.frame_config.get("margin_x", 0), # Using .get for safety on optional keys
                "marginY": self.frame_config.get("margin_y", 0),
                "frames": frames_for_card_obj,
                "artSource": art_crop_url,
                "artX": art_x,
                "artY": art_y,
                "artZoom": art_zoom,
                "artRotate": self.frame_config["art_rotate"],
                "setSymbolSource": f"{ccProto}://{ccHost}:{ccPort}/img/setSymbols/official/{set_code_for_symbol_url}-{rarity_code_for_symbol}.svg",
                "setSymbolX":set_symbol_x,
                "setSymbolY": set_symbol_y,
                "setSymbolZoom": set_symbol_zoom,
                "watermarkSource": f"{ccProto}://{ccHost}:{ccPort}/{self.frame_config['watermark_source']}",
                "watermarkX": self.frame_config["watermark_x"],
                "watermarkY": self.frame_config["watermark_y"],
                "watermarkZoom": self.frame_config["watermark_zoom"],
                "watermarkLeft": self.frame_config["watermark_left"],
                "watermarkRight": self.frame_config["watermark_right"],
                "watermarkOpacity": self.frame_config["watermark_opacity"],
                "version": self.frame_config.get("version_string", self.frame_type), # Use version_string from config
                "showsFlavorBar": self.frame_config.get("shows_flavor_bar", False), # Use from config
                "manaSymbols": mana_symbols,
                "infoYear": DEFAULT_INFO_YEAR,
                "margins": self.frame_config.get("margins", False),
                "bottomInfoTranslate": self.frame_config.get("bottomInfoTranslate", {"x": 0, "y": 0}),
                "bottomInfoRotate": self.frame_config.get("bottomInfoRotate", 0),
                "bottomInfoZoom": self.frame_config.get("bottomInfoZoom", 1),
                "bottomInfoColor": self.frame_config.get("bottomInfoColor", "white"),
                "onload": self.frame_config.get("onload", None),
                "hideBottomInfoBorder": self.frame_config.get("hideBottomInfoBorder", False),
                "showsFlavorBar": False,
                "bottomInfo": self.frame_config.get("bottom_info", {}),
                "artBounds": self.frame_config.get("art_bounds", {}),
                "setSymbolBounds": self.frame_config.get("set_symbol_bounds", {}),
                "watermarkBounds": self.frame_config.get("watermark_bounds", {}),
                "text": {
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
                        "text": card_data.get('oracle_text', '')
                    },
                    "pt": {
                        **self.frame_config.get("text", {}).get("pt", {}),
                        "text": f"{card_data.get('power', '')}/{card_data.get('toughness', '')}" if 'power' in card_data and 'toughness' in card_data else ""
                    }
                },
                "infoNumber": DEFAULT_INFO_NUMBER,
                # infoRarity uses the mapped rarity code, uppercased
                "infoRarity": rarity_code_for_symbol.upper() if rarity_code_for_symbol else DEFAULT_INFO_RARITY,
                # infoSet ALWAYS uses the actual Scryfall set code, uppercased
                "infoSet": set_code_from_scryfall.upper(),
                "infoLanguage": DEFAULT_INFO_LANGUAGE,
                "infoArtist": artist_name,
                "infoNote": DEFAULT_INFO_NOTE,
                "noCorners": self.frame_config.get("noCorners", True)
            }
        }
        
        # Add 8th edition specific fields if needed
        if self.frame_type == "8th":
            card_obj["data"]["serialNumber"] = ""
            card_obj["data"]["serialTotal"] = ""
            card_obj["data"]["serialX"] = ""
            card_obj["data"]["serialY"] = ""
            card_obj["data"]["serialScale"] = ""
        
        return card_obj
