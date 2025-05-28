# --- file: card_builder.py ---
"""
Module for building card data structure from Scryfall data
"""
import logging
from typing import Dict, List, Optional, Union, Tuple
import io 
import re 
import json
import time
import os
import base64
import unicodedata # For sanitizing filenames

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

def sanitize_for_filename(value: str) -> str:
    """
    Sanitizes a string to be safe for filenames and URL paths.
    Converts to lowercase, replaces spaces and special characters with hyphens.
    """
    if not isinstance(value, str):
        value = str(value)
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[\s/:<>:"\\|?*]+', '-', value)
    value = re.sub(r'-+', '-', value).strip('-')
    return value.lower()


class CardBuilder:
    """Class for building card data from Scryfall data"""
    
    def __init__(self, frame_type: str, frame_config: Dict, frame_set: str = "regular", 
                 legendary_crowns: bool = False, auto_fit_art: bool = False, 
                 set_symbol_override: Optional[str] = None, auto_fit_set_symbol: bool = False, 
                 api_delay_seconds: float = 0.1,
                 upscale_art: bool = False,
                 
                 # Ilaria Upscaler params
                 ilaria_upscaler_base_url: Optional[str] = None, 
                 upscaler_model_name: str = "RealESRGAN_x2plus", 
                 upscaler_outscale_factor: int = 2, 
                 upscaler_denoise_strength: float = 0.5, 
                 upscaler_face_enhance: bool = False,

                 # Nginx WebDAV Image Hosting params
                 image_server_base_url: Optional[str] = None, 
                 image_server_path_prefix: str = "/webdav_images" 
                ):
        self.frame_type = frame_type
        self.frame_config = frame_config
        self.frame_set = frame_set
        self.legendary_crowns = legendary_crowns
        self.auto_fit_art = auto_fit_art
        self.set_symbol_override = set_symbol_override
        self.auto_fit_set_symbol = auto_fit_set_symbol
        self.api_delay_seconds = api_delay_seconds
        self.upscale_art = upscale_art
        
        self.ilaria_upscaler_base_url = ilaria_upscaler_base_url
        self.upscaler_model_name = upscaler_model_name
        self.upscaler_outscale_factor = upscaler_outscale_factor
        self.upscaler_denoise_strength = upscaler_denoise_strength
        self.upscaler_face_enhance = upscaler_face_enhance

        self.image_server_base_url = image_server_base_url
        self.image_server_path_prefix = "/" + image_server_path_prefix.strip("/") + "/" if image_server_path_prefix else "/"
        
        if self.upscale_art and (not self.ilaria_upscaler_base_url or not self.image_server_base_url):
            logger.warning("Upscaling is enabled, but Ilaria Upscaler URL or Image Server URL is not configured. Upscaling/hosting will be skipped if a URL is missing.")

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

    def _calculate_auto_fit_set_symbol_params(self, set_symbol_url: str) -> Optional[Dict[str, any]]:
        set_code = self._extract_set_code_from_url(set_symbol_url)
        if set_code:
            lookup_key = f"{set_code}-{self.frame_type.lower()}"
            if lookup_key in self.symbol_placement_lookup:
                fixed_params = self.symbol_placement_lookup[lookup_key]
                if isinstance(fixed_params, dict) and all(k in fixed_params for k in ('x', 'y', 'zoom')):
                    return { "setSymbolX": fixed_params['x'], "setSymbolY": fixed_params['y'], "setSymbolZoom": fixed_params['zoom'], "_status": "success_lookup" }
                else: logger.warning(f"Invalid data for '{lookup_key}' in symbol_placements.json.")
            # else: logger.info(f"No fixed placement for '{lookup_key}' in lookup. Calculating.") # Can be noisy
        # else: logger.warning(f"Could not extract set_code from URL '{set_symbol_url}'. Calculating.") # Can be noisy

        try:
            response = requests.get(set_symbol_url, timeout=10); response.raise_for_status()
            svg_bytes = response.content
            if self.api_delay_seconds > 0 and (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True):
                time.sleep(self.api_delay_seconds)
            svg_dims = self._get_svg_dimensions(svg_bytes)
            if not svg_dims or svg_dims["width"] <= 0 or svg_dims["height"] <= 0:
                logger.warning(f"Could not determine valid SVG dimensions for {set_symbol_url}. Using defaults.")
                return {"_status": "calculation_issue_default_fallback"}
            
            card_total_width = self.frame_config.get("width")
            card_total_height = self.frame_config.get("height")
            symbol_bounds_config = self.frame_config.get("set_symbol_bounds")
            target_align_x_right_rel = self.frame_config.get("set_symbol_align_x_right")
            target_align_y_center_rel = self.frame_config.get("set_symbol_align_y_center")

            if not (card_total_width and card_total_height and symbol_bounds_config and 
                    isinstance(symbol_bounds_config, dict) and all(k in symbol_bounds_config for k in ('x', 'y', 'width', 'height')) and
                    target_align_x_right_rel is not None and target_align_y_center_rel is not None):
                logger.warning(f"Frame config incomplete for set symbol auto-fit with {set_symbol_url}. Using defaults.")
                return {"_status": "calculation_issue_default_fallback"}
            
            scale_x_factor = (symbol_bounds_config["width"] * card_total_width) / svg_dims["width"]
            scale_y_factor = (symbol_bounds_config["height"] * card_total_height) / svg_dims["height"]
            calculated_zoom = min(scale_x_factor, scale_y_factor)
            if calculated_zoom <= 1e-6: 
                logger.warning(f"Calculated zoom for set symbol too small for {set_symbol_url}. Using defaults.")
                return {"_status": "calculation_issue_default_fallback"}

            scaled_width_rel = (svg_dims["width"] * calculated_zoom) / card_total_width
            scaled_height_rel = (svg_dims["height"] * calculated_zoom) / card_total_height
            return { "setSymbolX": target_align_x_right_rel - scaled_width_rel, 
                     "setSymbolY": target_align_y_center_rel - (scaled_height_rel / 2.0), 
                     "setSymbolZoom": calculated_zoom, "_status": "success_calculated" }
        except requests.RequestException as e: logger.error(f"Symbol SVG request error for {set_symbol_url}: {e}"); return {"_status": "fetch_error"}
        except Exception as e: logger.error(f"Symbol auto-fit error for {set_symbol_url}: {e}", exc_info=True); return {"_status": "processing_error"}

    def _get_svg_dimensions(self, svg_content_bytes: bytes) -> Optional[Dict[str, float]]:
        if not svg_content_bytes: return None
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True) # Added recover=True
            svg_root = etree.fromstring(svg_content_bytes, parser=parser)
            if svg_root is None or not svg_root.tag.endswith('svg'): # Check if parsing failed
                 logger.warning("Failed to parse SVG or root is not <svg> tag.")
                 return None
            viewbox_str, width_str, height_str = svg_root.get("viewBox"), svg_root.get("width"), svg_root.get("height")
            w, h = None, None
            if viewbox_str: 
                try: parts = [float(p) for p in re.split(r'[,\s]+', viewbox_str.strip())]; w, h = (parts[2], parts[3]) if len(parts) == 4 else (None, None)
                except ValueError: logger.warning(f"Could not parse viewBox: '{viewbox_str}'")
            if w is None and width_str and not width_str.endswith('%'): 
                try: w = float(re.sub(r'[^\d\.\-e]', '', width_str)) # Allow scientific notation and negatives
                except ValueError: logger.warning(f"Could not parse width: '{width_str}'")
            if h is None and height_str and not height_str.endswith('%'): 
                try: h = float(re.sub(r'[^\d\.\-e]', '', height_str))
                except ValueError: logger.warning(f"Could not parse height: '{height_str}'")
            return {"width": w, "height": h} if w and h and w > 0 and h > 0 else None
        except etree.XMLSyntaxError as xml_err: logger.error(f"XMLSyntaxError parsing SVG: {xml_err}"); return None
        except Exception as e: logger.error(f"General error parsing SVG dimensions: {e}", exc_info=True); return None


    def _calculate_auto_fit_art_params_from_data(self, image_bytes: bytes, art_url_for_logging: str) -> Optional[Dict[str, float]]:
        if not image_bytes: logger.warning(f"No image bytes for art auto-fit: {art_url_for_logging}"); return None
        try:
            img = Image.open(io.BytesIO(image_bytes)); w, h = img.width, img.height; img.close()
            if w == 0 or h == 0: logger.warning(f"Zero dimensions for art: {art_url_for_logging}"); return None
            
            cfg = self.frame_config; card_w, card_h = cfg.get("width"), cfg.get("height")
            art_bounds = cfg.get("art_bounds")
            if not (card_w and card_h and art_bounds and isinstance(art_bounds, dict) and all(k in art_bounds for k in ('x', 'y', 'width', 'height'))):
                logger.warning(f"Incomplete frame/art_bounds config for art auto-fit: {art_url_for_logging}"); return None
            
            box_rel_x, box_rel_y, box_rel_w, box_rel_h = art_bounds["x"], art_bounds["y"], art_bounds["width"], art_bounds["height"]
            if box_rel_w <= 0 or box_rel_h <= 0: logger.warning(f"Invalid art_bounds dimensions for auto-fit: {art_url_for_logging}"); return None
            
            target_abs_w, target_abs_h = box_rel_w * card_w, box_rel_h * card_h
            scale_x, scale_y = target_abs_w / w, target_abs_h / h
            zoom = max(scale_x, scale_y)
            if zoom <= 1e-6: logger.warning(f"Calculated art zoom too small for auto-fit: {art_url_for_logging}"); return None
            
            art_x = box_rel_x + (target_abs_w - w * zoom) / 2 / card_w
            art_y = box_rel_y + (target_abs_h - h * zoom) / 2 / card_h
            return {"artX": art_x, "artY": art_y, "artZoom": zoom}
        except Exception as e: logger.error(f"Art auto-fit from data error for {art_url_for_logging}: {e}", exc_info=True); return None

    def _calculate_auto_fit_art_params(self, art_url: str) -> Optional[Dict[str, float]]:
        if not art_url: return None
        try:
            response = requests.get(art_url, timeout=10); response.raise_for_status()
            if self.api_delay_seconds > 0 and (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True):
                time.sleep(self.api_delay_seconds)
            return self._calculate_auto_fit_art_params_from_data(response.content, art_url)
        except requests.RequestException as e: logger.error(f"Art auto-fit URL fetch error for {art_url}: {e}"); return None
        except Image.UnidentifiedImageError as img_err: logger.error(f"Cannot identify image for art auto-fit from {art_url}: {img_err}"); return None
        except Exception as e: logger.error(f"Unexpected error in art auto-fit for {art_url}: {e}", exc_info=True); return None


    def _get_image_mime_type_and_extension(self, image_bytes: bytes) -> tuple[Optional[str], Optional[str]]:
        try:
            img_format = None
            try: img = Image.open(io.BytesIO(image_bytes)); img_format = img.format; img.close()
            except Exception: logger.debug("Pillow could not determine image format, trying manual sniff.")
            
            if img_format == "JPEG": return "image/jpeg", ".jpg"
            if img_format == "PNG": return "image/png", ".png"
            if img_format == "GIF": return "image/gif", ".gif"
            if img_format == "WEBP": return "image/webp", ".webp"
            
            # Fallback manual sniffing
            if image_bytes.startswith(b'\xff\xd8\xff'): return "image/jpeg", ".jpg"
            if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'): return "image/png", ".png"
            if image_bytes.startswith(b'GIF87a') or image_bytes.startswith(b'GIF89a'): return "image/gif", ".gif"
            if image_bytes.startswith(b'RIFF') and len(image_bytes) > 12 and image_bytes[8:12] == b'WEBP': return "image/webp", ".webp"
            
            logger.warning(f"Unsupported image format for MIME type or extension detection (first 10 bytes: {image_bytes[:10].hex()}).")
            return "application/octet-stream", "" # Generic fallback
        except Exception as e: logger.error(f"Could not determine image type: {e}", exc_info=True); return "application/octet-stream", ""

    def _upscale_image_with_ilaria(self, 
                                   hosted_original_image_url: str, 
                                   original_base_filename: str, 
                                   original_image_mime_type: Optional[str]
                                  ) -> Optional[bytes]:
        if not self.ilaria_upscaler_base_url:
            logger.error("Ilaria Upscaler base URL not configured. Skipping upscale."); return None
        if not hosted_original_image_url:
            logger.warning(f"Upscaling for '{original_base_filename}' skipped: No hosted original image URL provided."); return None

        ilaria_api_realesrgan_url = f"{self.ilaria_upscaler_base_url.rstrip('/')}/api/realesrgan"
        
        image_descriptor = {
            "path": hosted_original_image_url, "url": hosted_original_image_url,
            "orig_name": original_base_filename, "size": None, 
            "mime_type": original_image_mime_type if original_image_mime_type else "application/octet-stream",
            "is_stream": False
        }
        payload_data = [image_descriptor, self.upscaler_model_name, self.upscaler_denoise_strength,
                        self.upscaler_face_enhance, float(self.upscaler_outscale_factor)]
        payload = {"data": payload_data}
        logger.info(f"Upscaling '{original_base_filename}' via Ilaria: {ilaria_api_realesrgan_url} using URL: {hosted_original_image_url}")

        try:
            response = requests.post(ilaria_api_realesrgan_url, json=payload, timeout=180); response.raise_for_status()
            api_response_data = response.json()
            if 'data' in api_response_data and isinstance(api_response_data['data'], list) and api_response_data['data']:
                output_desc = api_response_data['data'][0]
                if isinstance(output_desc, dict) and output_desc.get('url'):
                    temp_upscaled_url = output_desc['url']
                    logger.info(f"Upscaled image (temp) for '{original_base_filename}' at: {temp_upscaled_url}. Fetching...")
                    upscaled_response = requests.get(temp_upscaled_url, timeout=60); upscaled_response.raise_for_status()
                    logger.info(f"Fetched upscaled image for '{original_base_filename}'.")
                    return upscaled_response.content
            logger.error(f"Ilaria API unexpected output descriptor for '{original_base_filename}': {str(api_response_data.get('data', ['N/A'])[0])[:200]}"); return None
        except requests.exceptions.HTTPError as e:
            content = e.response.text[:500] if e.response else "N/A"
            logger.error(f"Ilaria API HTTP error for '{original_base_filename}': {e}. Response: {content}"); return None
        except Exception as e: logger.error(f"Ilaria API general error for '{original_base_filename}': {e}", exc_info=True); return None

    def _host_image_to_nginx_webdav(self, image_bytes: bytes, 
                                   sub_directory: str, base_filename: str) -> Optional[str]:
        if not self.image_server_base_url:
            logger.error(f"Image Server URL not set. Cannot host '{base_filename}'."); return None
        if not image_bytes: logger.warning(f"No bytes to host for '{base_filename}'."); return None

        full_path_segment = (f"{self.image_server_path_prefix.strip('/')}/"
                             f"{sub_directory.strip('/')}/"
                             f"{base_filename.lstrip('/')}")
        if not full_path_segment.startswith('/'): full_path_segment = '/' + full_path_segment
        
        upload_url = f"{self.image_server_base_url.rstrip('/')}{full_path_segment}"
        logger.info(f"Hosting '{base_filename}' to Nginx WebDAV: {upload_url}")
        mime_type, _ = self._get_image_mime_type_and_extension(image_bytes)
        headers = {'Content-Type': mime_type if mime_type else 'application/octet-stream'}

        try:
            response = requests.put(upload_url, data=image_bytes, headers=headers, timeout=60)
            response.raise_for_status()
            if 200 <= response.status_code < 300:
                logger.info(f"Hosted '{base_filename}' successfully. URL: {upload_url}")
                return upload_url
            logger.error(f"Nginx hosting error for '{base_filename}': Status {response.status_code}"); return None
        except requests.exceptions.HTTPError as e:
            content = e.response.text[:500] if e.response else "N/A"
            logger.error(f"Nginx hosting HTTP error for '{base_filename}': {e}. Response: {content}"); return None
        except Exception as e: logger.error(f"Nginx hosting network error for '{base_filename}': {e}", exc_info=True); return None
    
    def _format_path(self, path_format_str: Optional[str], **kwargs) -> str:
        if not path_format_str:
            is_optional_pt = 'pt_path_format' in str(kwargs.get('caller_description', '')) and kwargs.get('path_type_optional', False)
            if not is_optional_pt: logger.error(f"Path format string is None/empty. Args: {kwargs}")
            return "/img/error_path.png" 
        valid_args = {k: v for k, v in kwargs.items() if f"{{{k}}}" in path_format_str}
        try: return path_format_str.format(**valid_args)
        except Exception as e: logger.error(f"Path format error '{path_format_str}' with {valid_args}: {e}"); return "/img/error_path_generic.png"

    def build_frame_path(self, color_code: str) -> str:
        return self._format_path(self.frame_config.get("frame_path_format"), frame=self.frame_type, frame_set=self.frame_set, color_code=color_code.lower())
    
    def build_mask_path(self, mask_name: str) -> str:
        if self.frame_type == "8th": return f"/img/frames/8th/{mask_name}{'.svg' if mask_name == 'border' else '.png'}"
        return self._format_path(self.frame_config.get("mask_path_format"), frame=self.frame_type, frame_set=self.frame_set, mask_name=mask_name)
    
    def build_land_frame_path(self, color_code: str) -> str:
        if self.frame_type == "8th": return f"/img/frames/8th/{color_code.lower()}l.png" 
        cfg = self.frame_config
        if cfg.get("uses_frame_set", False): 
            return f"/img/frames/{self.frame_type}/{self.frame_set}/" + cfg.get("land_color_format", "{color_code}l.png").format(color_code=color_code.lower())
        if "land_frame_path_format" in cfg: return self._format_path(cfg["land_frame_path_format"], color_code=color_code.lower())
        main_fmt = cfg.get("frame_path_format")
        if main_fmt: return main_fmt.rsplit('/', 1)[0] + "/" + cfg.get("land_color_format", "{color_code}l.png").format(color_code=color_code.lower())
        logger.error(f"Cannot determine land frame path for {self.frame_type} color {color_code}"); return "/img/error_land_frame.png"

    def build_pt_frame_path(self, color_code: str) -> Optional[str]:
        return self._format_path(self.frame_config.get("pt_path_format"), path_type_optional=True, frame=self.frame_type, color_code=color_code, color_code_upper=color_code.upper(), color_code_lower=color_code.lower())

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

    def build_m15ub_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:
        generated_frames = []
        card_name_for_logging = card_data.get('name', 'Unknown Card')
        type_line = card_data.get('type_line', '')
        is_land_card = 'Land' in type_line

        if 'power' in card_data and 'toughness' in card_data:
            pt_code_to_use = None 
            pt_name_prefix = "Unknown"
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
        
        is_legendary = 'Legendary' in type_line
        if self.legendary_crowns and is_legendary:
            primary_crown_color_code, secondary_crown_color_code = None, None
            primary_crown_color_name, secondary_crown_color_name = "Legend", "Secondary"
            if isinstance(color_info, dict) and color_info.get('is_gold') and color_info.get('component_colors'):
                components = color_info['component_colors']
                if len(components) >= 1: primary_crown_color_code, primary_crown_color_name = components[0]['code'], components[0]['name']
                if len(components) >= 2: secondary_crown_color_code, secondary_crown_color_name = components[1]['code'], components[1]['name']
            elif isinstance(color_info, dict) and color_info.get('code'): primary_crown_color_code, primary_crown_color_name = color_info['code'], color_info['name']
            elif is_land_card and isinstance(color_info, list):
                if len(color_info) > 1 and isinstance(color_info[1], dict) and 'code' in color_info[1]: primary_crown_color_code, primary_crown_color_name = color_info[1]['code'], color_info[1]['name']
                if len(color_info) > 2 and isinstance(color_info[2], dict) and 'code' in color_info[2]: secondary_crown_color_code, secondary_crown_color_name = color_info[2]['code'], color_info[2]['name']
                elif not primary_crown_color_code and len(color_info) == 1 and isinstance(color_info[0], dict) and 'code' in color_info[0]: primary_crown_color_code, primary_crown_color_name = color_info[0]['code'], color_info[0]['name']
            
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

        if is_land_card and isinstance(color_info, list):
            ttfb_code, ttfb_name = base_codes.get('L'), base_names.get('L', "Land")
            if len(color_info) > 1 and isinstance(color_info[1], dict) and 'code' in color_info[1]: 
                primary_color_code_main, primary_color_name_main = color_info[1]['code'], color_info[1]['name']
                if len(color_info) > 2 and isinstance(color_info[2], dict) and 'code' in color_info[2]: 
                    secondary_color_code_main, secondary_color_name_main = color_info[2]['code'], color_info[2]['name']
            elif len(color_info) == 1 and isinstance(color_info[0], dict) and 'code' in color_info[0]: 
                primary_color_code_main, primary_color_name_main = color_info[0]['code'], color_info[0]['name']
            else: 
                logger.warning(f"Unexpected color_info structure for land '{card_name_for_logging}': {color_info}. Defaulting primary_color_code_main to 'l'.")
                primary_color_code_main, primary_color_name_main = base_codes.get('L'), base_names.get('L', "Land")
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
            generated_frames.extend(main_frame_layers); return generated_frames
        if not ttfb_code: 
            logger.warning(f"M15UB MainFrame: TTFB code missing for '{card_name_for_logging}', falling back to primary. color_info: {color_info}");
            ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main 

        src_pinline_rules = ""; src_type_title = ""; src_frame_border = ""
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
                logger.warning(f"M15UB MainFrame: Error generating secondary path for '{card_name_for_logging}'. Falling back to primary layers.")
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

    def build_card_data(self, card_name: str, card_data: Dict, color_info, 
                        is_basic_land_fetch_mode: bool = False,
                        basic_land_type_override: Optional[str] = None) -> Dict:
    
        logger.debug(f"build_card_data for '{card_name}', frame_type '{self.frame_type}'. Upscale Art: {self.upscale_art}")
        
        frames_for_card_obj = []
        if self.frame_type == "8th": frames_for_card_obj = self.build_eighth_edition_frames(color_info, card_data)
        elif self.frame_type == "m15": frames_for_card_obj = self.build_m15_frames(color_info, card_data)
        elif self.frame_type == "m15ub": frames_for_card_obj = self.build_m15ub_frames(color_info, card_data)
        else: frames_for_card_obj = self.build_seventh_edition_frames(color_info, card_data)
        
        mana_symbols = []
        if isinstance(color_info, list) or self.frame_config.get("version_string", "") == "m15EighthSnow": 
            mana_symbols = ["/js/frames/manaSymbolsFAB.js", "/js/frames/manaSymbolsBreakingNews.js"]
            if self.frame_type == "seventh": mana_symbols = ["/js/frames/manaSymbolsFuture.js", "/js/frames/manaSymbolsOld.js"]

        oracle_text_from_scryfall = card_data.get('oracle_text', '')
        flavor_text_from_scryfall = card_data.get('flavor_text') 
        final_rules_text = oracle_text_from_scryfall
        shows_flavor_bar_for_this_card = self.frame_config.get("shows_flavor_bar", False) 
        if is_basic_land_fetch_mode and basic_land_type_override:
            produced = card_data.get("produced_mana")
            if produced and isinstance(produced, list) and len(produced) > 0:
                mana_char = produced[0]; final_rules_text = f"{{fontsize450}}{{center}}{{down90}}{{{mana_char.lower()}}}"
                shows_flavor_bar_for_this_card = False 
            else: final_rules_text = ""
        elif flavor_text_from_scryfall:
            cleaned_flavor_text = flavor_text_from_scryfall.replace('*', '')
            final_rules_text = (final_rules_text + "\n{flavor}" if final_rules_text else "{flavor}") + cleaned_flavor_text
            shows_flavor_bar_for_this_card = True
        
        scryfall_card_name = card_data.get('name', card_name) 
        set_code_from_scryfall = card_data.get('set', DEFAULT_INFO_SET)
        collector_number_from_scryfall = card_data.get('collector_number', '000')
        artist_name = card_data.get('artist', DEFAULT_INFO_ARTIST)
        
        scryfall_art_crop_url = ""
        if 'image_uris' in card_data and 'art_crop' in card_data['image_uris']: scryfall_art_crop_url = card_data['image_uris']['art_crop']
        elif 'card_faces' in card_data and card_data['card_faces']:
            for face in card_data['card_faces']:
                if 'image_uris' in face and 'art_crop' in face['image_uris']: 
                    scryfall_art_crop_url = face['image_uris']['art_crop']; break
        
        final_art_source_url = scryfall_art_crop_url 
        hosted_original_art_url = None
        hosted_upscaled_art_url = None 

        art_x = self.frame_config.get("art_x", 0.0); art_y = self.frame_config.get("art_y", 0.0)
        art_zoom = self.frame_config.get("art_zoom", 1.0); art_rotate = self.frame_config.get("art_rotate", "0")
        original_image_content = None
        original_image_mime_type: Optional[str] = None
        image_ext = ".jpg" 

        if scryfall_art_crop_url:
            sanitized_card_name = sanitize_for_filename(scryfall_card_name)
            set_code_sanitized = sanitize_for_filename(set_code_from_scryfall)
            collector_number_sanitized = sanitize_for_filename(collector_number_from_scryfall)
            
            # Determine initial extension from URL first (robustly)
            try:
                url_path_no_query = scryfall_art_crop_url.split('?')[0]
                base, ext_from_url = os.path.splitext(url_path_no_query)
                if ext_from_url and ext_from_url.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                    image_ext = ext_from_url.lower()
            except Exception:
                logger.debug(f"Could not reliably parse extension from URL: {scryfall_art_crop_url}")


            # Fetch image data if needed for auto-fit or upscale/hosting
            if self.auto_fit_art or (self.upscale_art and self.image_server_base_url and self.ilaria_upscaler_base_url):
                try:
                    logger.debug(f"Fetching art from {scryfall_art_crop_url} for {set_code_sanitized}-{collector_number_sanitized}-{sanitized_card_name}.")
                    response = requests.get(scryfall_art_crop_url, timeout=10); response.raise_for_status()
                    if self.api_delay_seconds > 0 and (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True):
                        time.sleep(self.api_delay_seconds)
                    original_image_content = response.content
                    original_image_mime_type, determined_ext = self._get_image_mime_type_and_extension(original_image_content)
                    if determined_ext: image_ext = determined_ext # Override with more accurate extension from bytes
                    # If determined_ext is None, image_ext retains its URL-derived or default value
                    
                except Exception as e: 
                    logger.error(f"Failed to fetch art from {scryfall_art_crop_url}: {e}", exc_info=True)
                    # Keep image_ext as derived from URL or default if fetch fails

            if not image_ext.startswith('.'): image_ext = '.' + image_ext
            base_art_filename = f"{set_code_sanitized}-{collector_number_sanitized}-{sanitized_card_name}{image_ext}"

            if self.auto_fit_art:
                auto_fit_params = None
                if original_image_content: auto_fit_params = self._calculate_auto_fit_art_params_from_data(original_image_content, scryfall_art_crop_url)
                else: auto_fit_params = self._calculate_auto_fit_art_params(scryfall_art_crop_url)
                if auto_fit_params: art_x, art_y, art_zoom = auto_fit_params["artX"], auto_fit_params["artY"], auto_fit_params["artZoom"]
                else: logger.warning(f"Auto-fit failed for {base_art_filename}.")

            if self.upscale_art and original_image_content and self.image_server_base_url and self.ilaria_upscaler_base_url:
                hosted_original_art_url = self._host_image_to_nginx_webdav(original_image_content, "original", base_art_filename)
                
                if hosted_original_art_url:
                    upscaled_image_bytes = self._upscale_image_with_ilaria(
                        hosted_original_art_url, base_art_filename, original_image_mime_type
                    )
                    if upscaled_image_bytes:
                        _, upscaled_ext = self._get_image_mime_type_and_extension(upscaled_image_bytes)
                        if not upscaled_ext: upscaled_ext = image_ext 
                        if not upscaled_ext.startswith('.'): upscaled_ext = '.' + upscaled_ext
                        
                        upscaled_art_filename = f"{set_code_sanitized}-{collector_number_sanitized}-{sanitized_card_name}{upscaled_ext}"
                        upscaler_model_sanitized = sanitize_for_filename(self.upscaler_model_name)
                        
                        hosted_upscaled_art_url = self._host_image_to_nginx_webdav(
                            upscaled_image_bytes, upscaler_model_sanitized, upscaled_art_filename
                        )
                        
                        if hosted_upscaled_art_url:
                            final_art_source_url = hosted_upscaled_art_url
                        else: 
                            logger.warning(f"Failed to host upscaled image for {base_art_filename}. Using hosted original.")
                            final_art_source_url = hosted_original_art_url 
                    else: 
                        logger.warning(f"Upscaling failed for {base_art_filename}. Using hosted original.")
                        final_art_source_url = hosted_original_art_url 
                else: 
                    logger.warning(f"Failed to host original image {base_art_filename}. Using Scryfall URL.")
                    final_art_source_url = scryfall_art_crop_url 
            elif self.upscale_art:
                 logger.warning(f"Upscaling/hosting skipped for {base_art_filename}: requirements not met (original content or server URLs missing).")
        
        set_symbol_x = self.frame_config.get("set_symbol_x", 0.0); set_symbol_y = self.frame_config.get("set_symbol_y", 0.0); set_symbol_zoom = self.frame_config.get("set_symbol_zoom", 0.1)
        rarity_from_scryfall = card_data.get('rarity', 'c')
        rarity_code_for_symbol = RARITY_MAP.get(rarity_from_scryfall, rarity_from_scryfall)
        set_code_for_symbol_url = self.set_symbol_override.lower() if self.set_symbol_override else set_code_from_scryfall.lower()
        actual_set_code_for_url = set_code_for_symbol_url 
        set_symbol_source_url = f"{ccProto}://{ccHost}:{ccPort}/img/setSymbols/official/{actual_set_code_for_url}-{rarity_code_for_symbol}.svg"
        if self.auto_fit_set_symbol and set_symbol_source_url:
            auto_fit_symbol_params_result = self._calculate_auto_fit_set_symbol_params(set_symbol_source_url)
            if auto_fit_symbol_params_result:
                status = auto_fit_symbol_params_result.get("_status")
                if status in ["success_lookup", "success_calculated"]:
                    set_symbol_x = auto_fit_symbol_params_result["setSymbolX"]; set_symbol_y = auto_fit_symbol_params_result["setSymbolY"]; set_symbol_zoom = auto_fit_symbol_params_result["setSymbolZoom"]
                elif status == "calculation_issue_default_fallback": logger.warning(f"Auto-fit for set symbol {set_symbol_source_url} resulted in fallback.")
            else: logger.warning(f"Auto-fit set symbol calculation returned None for {set_symbol_source_url}.")

        power_val = card_data.get('power', ''); toughness_val = card_data.get('toughness', '')
        pt_text_final = ""
        if 'power' in card_data and 'toughness' in card_data:
            if self.frame_type == "8th":
                replacement_asterisk = "X" 
                if power_val == "*": power_val = replacement_asterisk
                if toughness_val == "*": toughness_val = replacement_asterisk
            pt_text_final = f"{power_val}/{toughness_val}"
        
        display_title_text = basic_land_type_override if is_basic_land_fetch_mode and basic_land_type_override else scryfall_card_name

        card_obj_data = {
            "width": self.frame_config["width"], "height": self.frame_config["height"],
            "marginX": self.frame_config.get("margin_x", 0), "marginY": self.frame_config.get("margin_y", 0),
            "frames": frames_for_card_obj, 
            "artSource": final_art_source_url, 
            "artX": art_x, "artY": art_y, "artZoom": art_zoom, "artRotate": art_rotate,
            "artSourceOriginalScryfall": scryfall_art_crop_url, 
            **({"artSourceHostedOriginal": hosted_original_art_url} if hosted_original_art_url else {}),
            **({"artSourceHostedUpscaled": hosted_upscaled_art_url} if hosted_upscaled_art_url and hosted_upscaled_art_url != final_art_source_url else {}),
            "setSymbolSource": set_symbol_source_url, "setSymbolX":set_symbol_x, "setSymbolY": set_symbol_y, "setSymbolZoom": set_symbol_zoom,
            "watermarkSource": f"{ccProto}://{ccHost}:{ccPort}/{self.frame_config['watermark_source']}",
            "watermarkX": self.frame_config["watermark_x"], "watermarkY": self.frame_config["watermark_y"], "watermarkZoom": self.frame_config["watermark_zoom"], 
            "watermarkLeft": self.frame_config["watermark_left"], "watermarkRight": self.frame_config["watermark_right"], "watermarkOpacity": self.frame_config["watermark_opacity"],
            "version": self.frame_config.get("version_string", self.frame_type), 
            "showsFlavorBar": shows_flavor_bar_for_this_card, "manaSymbols": mana_symbols,
            "infoYear": DEFAULT_INFO_YEAR, "margins": self.frame_config.get("margins", False),
            "bottomInfoTranslate": self.frame_config.get("bottomInfoTranslate", {"x": 0, "y": 0}), "bottomInfoRotate": self.frame_config.get("bottomInfoRotate", 0),
            "bottomInfoZoom": self.frame_config.get("bottomInfoZoom", 1), "bottomInfoColor": self.frame_config.get("bottomInfoColor", "white"),
            "onload": self.frame_config.get("onload", None), "hideBottomInfoBorder": self.frame_config.get("hideBottomInfoBorder", False),
            "bottomInfo": self.frame_config.get("bottom_info", {}), "artBounds": self.frame_config.get("art_bounds", {}),
            "setSymbolBounds": self.frame_config.get("set_symbol_bounds", {}), "watermarkBounds": self.frame_config.get("watermark_bounds", {}),
            "text": {
                "mana": {**self.frame_config.get("text", {}).get("mana", {}), "text": card_data.get('mana_cost', '')},
                "title": {**self.frame_config.get("text", {}).get("title", {}), "text": display_title_text},
                "type": {**self.frame_config.get("text", {}).get("type", {}), "text": card_data.get('type_line', 'Instant')},
                "rules": { **self.frame_config.get("text", {}).get("rules", {}), "text": final_rules_text },
                "pt": {**self.frame_config.get("text", {}).get("pt", {}), "text": pt_text_final }
            },
            "infoNumber": collector_number_from_scryfall, 
            "infoRarity": rarity_code_for_symbol.upper() if rarity_code_for_symbol else DEFAULT_INFO_RARITY, 
            "infoSet": set_code_from_scryfall.upper(), 
            "infoLanguage": DEFAULT_INFO_LANGUAGE, 
            "infoArtist": artist_name, 
            "infoNote": DEFAULT_INFO_NOTE,
            "noCorners": self.frame_config.get("noCorners", True)
        }
        if self.frame_type == "8th": card_obj_data.update({"serialNumber": "", "serialTotal": "", "serialX": "", "serialY": "", "serialScale": ""})
        
        return {"key": card_name, "data": card_obj_data}
