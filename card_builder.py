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
import unicodedata 
from pathlib import Path # NEW: Import Path for robust path handling

import requests 
from PIL import Image 
from lxml import etree 

from config import (
    ccProto, ccHost, ccPort,
    DEFAULT_INFO_YEAR, DEFAULT_INFO_RARITY, DEFAULT_INFO_SET,
    DEFAULT_INFO_LANGUAGE, DEFAULT_INFO_ARTIST, DEFAULT_INFO_NOTE, DEFAULT_INFO_NUMBER
)
from color_mapping import COLOR_CODE_MAP, RARITY_MAP

from gradio_client import Client, file as gradio_file

logger = logging.getLogger(__name__)

def sanitize_for_filename(value: str) -> str:
    if not isinstance(value, str): value = str(value)
    value = value.replace("'", "")
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[\s/:<>:"\\|?*&]+', '-', value)
    value = re.sub(r'-+', '-', value)
    value = value.strip('-')
    return value.lower()

class CardBuilder:
    """Class for building card data from Scryfall data"""
    
    # MODIFIED: Added output_dir to constructor
    def __init__(self, frame_type: str, frame_config: Dict, frame_set: str = "regular", 
                 legendary_crowns: bool = False, auto_fit_art: bool = False, 
                 set_symbol_override: Optional[str] = None, auto_fit_set_symbol: bool = False, 
                 api_delay_seconds: float = 0.1,
                 # Upscaling & Hosting Params
                 upscale_art: bool = False,
                 ilaria_upscaler_base_url: Optional[str] = None, 
                 upscaler_model_name: str = "RealESRGAN_x2plus", 
                 upscaler_outscale_factor: int = 2, 
                 upscaler_denoise_strength: float = 0.5, 
                 upscaler_face_enhance: bool = False,
                 # Output Params
                 image_server_base_url: Optional[str] = None, 
                 image_server_path_prefix: str = "/webdav_images",
                 output_dir: Optional[str] = None # NEW: Local output directory
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
        self.upscaler_outscale_factor = upscaler_outscale_factor if upscaler_outscale_factor > 0 else 1
        self.upscaler_denoise_strength = upscaler_denoise_strength
        self.upscaler_face_enhance = upscaler_face_enhance
        
        self.image_server_base_url = image_server_base_url
        self.image_server_path_prefix = "/" + image_server_path_prefix.strip("/") + "/" if image_server_path_prefix else "/"
        
        # NEW: Store output directory
        self.output_dir = output_dir

        # MODIFIED: Updated warning to include local saving option
        if self.upscale_art and not self.ilaria_upscaler_base_url:
            logger.warning("Upscaling is enabled, but --ilaria_base_url is not configured. Upscaling will be skipped.")
        if self.upscale_art and not self.image_server_base_url and not self.output_dir:
            logger.warning("Upscaling is enabled, but no output method (server URL or local directory) is configured. Processed art will not be saved.")

        self.symbol_placement_lookup = {}
        if self.auto_fit_set_symbol: 
            try:
                with open("symbol_placements.json", "r") as f: self.symbol_placement_lookup = json.load(f)
                logger.info(f"Loaded {len(self.symbol_placement_lookup)} entries from symbol_placements.json")
            except FileNotFoundError: logger.warning("symbol_placements.json not found.")
            except json.JSONDecodeError as e: logger.error(f"Error decoding symbol_placements.json: {e}.")

    # ... (methods from _extract_set_code_from_url to _get_svg_dimensions are unchanged) ...
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
        try:
            response = requests.get(set_symbol_url, timeout=10); response.raise_for_status()
            svg_bytes = response.content
            if self.api_delay_seconds > 0 and (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True): time.sleep(self.api_delay_seconds)
            svg_dims = self._get_svg_dimensions(svg_bytes)
            if not svg_dims or svg_dims["width"] <= 0 or svg_dims["height"] <= 0: logger.warning(f"Invalid SVG dimensions for {set_symbol_url}."); return {"_status": "calculation_issue_default_fallback"}
            card_w, card_h = self.frame_config.get("width"), self.frame_config.get("height")
            bounds_cfg = self.frame_config.get("set_symbol_bounds")
            align_x, align_y = self.frame_config.get("set_symbol_align_x_right"), self.frame_config.get("set_symbol_align_y_center")
            if not (card_w and card_h and bounds_cfg and isinstance(bounds_cfg, dict) and all(k in bounds_cfg for k in ('x', 'y', 'width', 'height')) and align_x is not None and align_y is not None):
                logger.warning(f"Frame config incomplete for set symbol auto-fit: {set_symbol_url}."); return {"_status": "calculation_issue_default_fallback"}
            scale_x = (bounds_cfg["width"] * card_w) / svg_dims["width"]; scale_y = (bounds_cfg["height"] * card_h) / svg_dims["height"]
            zoom = min(scale_x, scale_y)
            if zoom <= 1e-6: logger.warning(f"Set symbol zoom too small for {set_symbol_url}."); return {"_status": "calculation_issue_default_fallback"}
            scaled_w_rel = (svg_dims["width"] * zoom) / card_w; scaled_h_rel = (svg_dims["height"] * zoom) / card_h
            return { "setSymbolX": align_x - scaled_w_rel, "setSymbolY": align_y - (scaled_h_rel / 2.0), "setSymbolZoom": zoom, "_status": "success_calculated" }
        except requests.RequestException as e: logger.error(f"Symbol SVG request error for {set_symbol_url}: {e}"); return {"_status": "fetch_error"}
        except Exception as e: logger.error(f"Symbol auto-fit error for {set_symbol_url}: {e}", exc_info=True); return {"_status": "processing_error"}

    def _get_svg_dimensions(self, svg_content_bytes: bytes) -> Optional[Dict[str, float]]:
        if not svg_content_bytes: return None
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
            svg_root = etree.fromstring(svg_content_bytes, parser=parser)
            if svg_root is None or not svg_root.tag.endswith('svg'): logger.warning("Failed to parse SVG."); return None
            viewbox, width_str, height_str = svg_root.get("viewBox"), svg_root.get("width"), svg_root.get("height")
            w, h = None, None
            if viewbox: 
                try: p = [float(x) for x in re.split(r'[,\s]+', viewbox.strip())]; w, h = (p[2], p[3]) if len(p) == 4 else (None, None)
                except ValueError: logger.warning(f"Could not parse viewBox: '{viewbox}'")
            if w is None and width_str and not width_str.endswith('%'): 
                try: w = float(re.sub(r'[^\d\.\-e]', '', width_str))
                except ValueError: logger.warning(f"Could not parse width: '{width_str}'")
            if h is None and height_str and not height_str.endswith('%'): 
                try: h = float(re.sub(r'[^\d\.\-e]', '', height_str))
                except ValueError: logger.warning(f"Could not parse height: '{height_str}'")
            return {"width": w, "height": h} if w and h and w > 0 and h > 0 else None
        except Exception as e: logger.error(f"Error parsing SVG dimensions: {e}", exc_info=True); return None

    def _calculate_auto_fit_art_params_from_data(self, image_bytes: bytes, log_ref: str) -> Optional[Dict[str, float]]:
        if not image_bytes: logger.warning(f"No image bytes for art auto-fit: {log_ref}"); return None
        try:
            img = Image.open(io.BytesIO(image_bytes)); w, h = img.width, img.height; img.close()
            if w == 0 or h == 0: logger.warning(f"Zero dimensions for art: {log_ref}"); return None
            cfg = self.frame_config; card_w, card_h = cfg.get("width"), cfg.get("height")
            b = cfg.get("art_bounds")
            if not (card_w and card_h and b and isinstance(b, dict) and all(k in b for k in ('x', 'y', 'width', 'height'))):
                logger.warning(f"Incomplete config for art auto-fit: {log_ref}"); return None
            if b["width"] <= 0 or b["height"] <= 0: logger.warning(f"Invalid art_bounds for auto-fit: {log_ref}"); return None
            abs_w, abs_h = b["width"] * card_w, b["height"] * card_h
            zoom = max(abs_w / w, abs_h / h)
            if zoom <= 1e-6: logger.warning(f"Art zoom too small for auto-fit: {log_ref}"); return None
            return {"artX": b["x"] + (abs_w - w * zoom) / 2 / card_w, "artY": b["y"] + (abs_h - h * zoom) / 2 / card_h, "artZoom": zoom}
        except Exception as e: logger.error(f"Art auto-fit from data error for {log_ref}: {e}", exc_info=True); return None

    def _calculate_auto_fit_art_params(self, art_url: str) -> Optional[Dict[str, float]]: # From baseline
        if not art_url: return None
        try:
            logger.debug(f"Auto-fit: Fetching Scryfall art from {art_url} for dimension calculation.")
            response = requests.get(art_url, timeout=10); response.raise_for_status()
            if self.api_delay_seconds > 0 and (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True):
                time.sleep(self.api_delay_seconds)
            # This is where baseline _calculate_auto_fit_art_params used to have all its logic
            # Now it calls the _from_data helper
            return self._calculate_auto_fit_art_params_from_data(response.content, art_url)
        except Exception as e: logger.error(f"Error in _calculate_auto_fit_art_params for {art_url}: {e}", exc_info=True); return None

    def _fetch_image_bytes(self, url: str, purpose: str = "generic") -> Optional[bytes]: # General helper
        if not url: return None
        try:
            logger.debug(f"Fetching image for {purpose} from: {url}")
            response = requests.get(url, timeout=10); response.raise_for_status()
            if "scryfall.com" in url.lower() and self.api_delay_seconds > 0 and \
               (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True):
                time.sleep(self.api_delay_seconds)
            return response.content
        except Exception as e: logger.error(f"Failed to fetch image for {purpose} from {url}: {e}", exc_info=True); return None
            
    def _get_image_mime_type_and_extension(self, image_bytes: bytes) -> tuple[Optional[str], Optional[str]]:
        try:
            fmt = None; 
            try: img = Image.open(io.BytesIO(image_bytes)); fmt = img.format; img.close()
            except Exception: pass
            if fmt == "JPEG": return "image/jpeg", ".jpg"
            if fmt == "PNG": return "image/png", ".png"
            if fmt == "GIF": return "image/gif", ".gif"
            if fmt == "WEBP": return "image/webp", ".webp"
            if image_bytes.startswith(b'\xff\xd8\xff'): return "image/jpeg", ".jpg"
            if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'): return "image/png", ".png"
            if image_bytes.startswith(b'GIF87a') or image_bytes.startswith(b'GIF89a'): return "image/gif", ".gif"
            if image_bytes.startswith(b'RIFF') and len(image_bytes) > 12 and image_bytes[8:12] == b'WEBP': return "image/webp", ".webp"
            return "application/octet-stream", "" 
        except Exception: return "application/octet-stream", ""

    # MODIFIED: This function now handles both local and remote fetching of the original image
    def _upscale_image_with_ilaria(self, hosted_original_url_or_path: str, filename: str, mime: Optional[str]) -> Optional[bytes]:
        if not self.ilaria_upscaler_base_url:
            logger.error("Ilaria URL not set.")
            return None
        if not hosted_original_url_or_path:
            logger.warning(f"No hosted original URL or path for '{filename}'.")
            return None

        img_bytes = None
        # --- LOCAL MODE: Read from disk ---
        if self.output_dir:
            local_path = Path(self.output_dir) / hosted_original_url_or_path.lstrip('/')
            logger.debug(f"Upscaling: Reading original image from local path: {local_path}")
            try:
                with open(local_path, "rb") as f:
                    img_bytes = f.read()
            except FileNotFoundError:
                logger.error(f"Upscaling failed: Original image not found at local path {local_path}")
                return None
            except Exception as e:
                logger.error(f"Upscaling failed: Could not read local file {local_path}: {e}")
                return None
        # --- SERVER MODE: Fetch from URL ---
        else:
            img_bytes = self._fetch_image_bytes(hosted_original_url_or_path, "Upscaling with gradio_client")

        if not img_bytes:
            logger.error(f"Failed to get image bytes from {hosted_original_url_or_path}")
            return None

        try:
            logger.info(f"Connecting to Ilaria Upscaler via gradio_client.")
            client = Client("TheStinger/Ilaria_Upscaler")

            temp_path = f"/tmp/{sanitize_for_filename(filename)}"
            with open(temp_path, "wb") as f:
                f.write(img_bytes)

            logger.info(f"Upscaling {filename} using model '{self.upscaler_model_name}' via gradio_client.")
            result = client.predict(
                img=gradio_file(temp_path),
                model_name=self.upscaler_model_name,
                denoise_strength=self.upscaler_denoise_strength,
                face_enhance=self.upscaler_face_enhance,
                outscale=self.upscaler_outscale_factor,
                api_name="/realesrgan"
            )

            if isinstance(result, tuple):
                result_path = result[0]  # grab only the image path
            else:
                result_path = result

            logger.info(f"Upscaled image path: {result_path}")

            with open(result_path, "rb") as f:
                return f.read()

        except Exception as e:
            logger.error(f"Gradio upscaling error for '{filename}': {e}", exc_info=True)
            return None

    def _check_if_file_exists_on_server(self, public_url: str) -> bool:
        if not public_url: return False
        try:
            r = requests.head(public_url, timeout=15, allow_redirects=True) 
            if r.status_code == 200: logger.info(f"Exists: {public_url}"); return True
            if r.status_code == 404: logger.info(f"Not found: {public_url}"); return False
            logger.warning(f"Status {r.status_code} checking {public_url}. Assuming not existent."); return False 
        except Exception as e: logger.warning(f"Error checking {public_url}: {e}. Assuming not existent."); return False

    def _construct_nginx_public_url(self, sub_dir: str, filename: str) -> Optional[str]:
        if not self.image_server_base_url: return None
        path = (f"{self.image_server_path_prefix.strip('/')}/{sub_dir.strip('/')}/{filename.lstrip('/')}")
        if not path.startswith('/'): path = '/' + path
        return f"{self.image_server_base_url.rstrip('/')}{path}"

    # --- NEW METHOD: Centralized image saving/uploading logic ---
    def _save_image(self, img_bytes: bytes, sub_dir: str, filename: str) -> Optional[str]:
        """
        Saves an image either to a local directory or a WebDAV server.
        Returns the public-facing URL/path for the CardConjurer JSON.
        """
        if not img_bytes:
            logger.warning(f"No image bytes provided for '{filename}' in '{sub_dir}'. Cannot save.")
            return None

        # --- LOCAL FILE SYSTEM MODE ---
        if self.output_dir:
            try:
                # Construct the full local path
                # image_server_path_prefix is reused as the base subdirectory
                local_save_dir = Path(self.output_dir) / self.image_server_path_prefix.strip('/') / sub_dir.strip('/')
                local_save_dir.mkdir(parents=True, exist_ok=True)
                local_file_path = local_save_dir / filename

                # Save the file
                with open(local_file_path, 'wb') as f:
                    f.write(img_bytes)
                
                logger.info(f"Saved image locally to: {local_file_path}")
                
                # Return the relative path for the JSON file
                relative_path = f"{self.image_server_path_prefix.strip('/')}/{sub_dir.strip('/')}/{filename}"
                if not relative_path.startswith('/'):
                    relative_path = '/' + relative_path
                return relative_path

            except Exception as e:
                logger.error(f"Local save error for '{filename}': {e}", exc_info=True)
                return None

        # --- SERVER UPLOAD MODE (Original Logic) ---
        elif self.image_server_base_url:
            url = self._construct_nginx_public_url(sub_dir, filename)
            if not url:
                logger.error(f"Server URL not set. Cannot host '{filename}'.")
                return None
            
            logger.info(f"Hosting '{filename}' to Nginx: {url}")
            mime, _ = self._get_image_mime_type_and_extension(img_bytes)
            headers = {'Content-Type': mime if mime else 'application/octet-stream'}
            try:
                r = requests.put(url, data=img_bytes, headers=headers, timeout=60)
                r.raise_for_status()
                if 200 <= r.status_code < 300:
                    logger.info(f"Hosted '{filename}'. URL: {url}")
                    return url
                logger.error(f"Nginx hosting error for '{filename}': Status {r.status_code}.")
                return None
            except Exception as e:
                logger.error(f"Nginx hosting error for '{filename}': {e}", exc_info=True)
                return None
        
        # --- NO OUTPUT CONFIGURED ---
        else:
            logger.warning(f"No output method configured (local or server). Cannot save '{filename}'.")
            return None

    # MODIFIED: _host_image_to_nginx_webdav is now a simple wrapper around _save_image
    def _host_image_to_nginx_webdav(self, img_bytes: bytes, sub_dir: str, filename: str) -> Optional[str]:
        return self._save_image(img_bytes, sub_dir, filename)

    # ... (methods from _format_path to the end of the file are unchanged) ...
    def _format_path(self, path_format_str: Optional[str], **kwargs) -> str: # From baseline
        if not path_format_str:
            if not ('pt_path_format' in str(kwargs.get('caller_description', '')) and kwargs.get('path_type_optional', False)):
                 logger.error(f"Path format string is None or empty. Args: {kwargs}")
            return "/img/error_path.png" 
        valid_args = {k: v for k, v in kwargs.items() if f"{{{k}}}" in path_format_str}
        try: return path_format_str.format(**valid_args)
        except KeyError as e: logger.error(f"KeyError formatting path '{path_format_str}' with effectively used args {valid_args} (original args: {kwargs}): {e}"); return "/img/error_path_key_error.png"
        except Exception as e_gen: logger.error(f"Generic error formatting path '{path_format_str}' with args {valid_args}: {e_gen}"); return "/img/error_path_generic.png"

    # --- PASTE YOUR BASELINE FRAME BUILDERS HERE ---
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
                # For single-color lands: pinline, rules, and trim use colored land frame; frame and border use generic land frame
                frames.append({"name": f"{color['name']} Land Frame", "src": self.build_land_frame_path(color['code']), "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]})
                frames.append({"name": f"{color['name']} Land Frame", "src": self.build_land_frame_path(color['code']), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]})
                frames.append({"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]})
                frames.append({"name": f"{color['name']} Land Frame", "src": self.build_land_frame_path(color['code']), "masks": [{"src": self.build_mask_path("trim"), "name": "Textbox Pinline"}]})
                frames.append({"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]})
            else: frames = [{"name": f"{land_frame['name']} Frame", "src": self.build_frame_path(land_frame['code']), "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize() if mask_name != "trim" else "Textbox Pinline"}]} for mask_name in ["pinline", "rules"] + common_masks]
        else: 
            color_code, color_name = color_info['code'], color_info['name']
            frames = [{"name": f"{color_name} Frame", "src": self.build_frame_path(color_code), "masks": [{"src": self.build_mask_path(mask_name), "name": mask_name.capitalize() if mask_name != "trim" else "Textbox Pinline"}]} for mask_name in ["pinline", "rules"] + common_masks]
        return frames

    def build_m15ub_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:
        generated_frames = []; card_name_for_logging = card_data.get('name', 'Unknown Card'); type_line = card_data.get('type_line', ''); is_land_card = 'Land' in type_line
        if 'power' in card_data and 'toughness' in card_data:
            pt_code_to_use, pt_name_prefix = None, "Unknown"
            if 'Vehicle' in type_line: pt_code_to_use, pt_name_prefix = COLOR_CODE_MAP.get('V', {}).get('code'), COLOR_CODE_MAP.get('V', {}).get('name', "Vehicle")
            elif isinstance(color_info, dict):
                is_gold, is_artifact = color_info.get('is_gold', False), color_info.get('is_artifact', False)
                is_true_colorless = color_info.get('code') == COLOR_CODE_MAP.get('C',{}).get('code') and not is_artifact
                if is_gold: pt_code_to_use, pt_name_prefix = COLOR_CODE_MAP.get('M', {}).get('code'), COLOR_CODE_MAP.get('M', {}).get('name', "Multicolored")
                elif is_artifact: pt_code_to_use, pt_name_prefix = COLOR_CODE_MAP.get('A', {}).get('code'), COLOR_CODE_MAP.get('A', {}).get('name', "Artifact")
                elif is_true_colorless: pt_code_to_use, pt_name_prefix = COLOR_CODE_MAP.get('C', {}).get('code'), COLOR_CODE_MAP.get('C', {}).get('name', "Colorless")
                elif color_info.get('code') in ['w','u','b','r','g']: pt_code_to_use, pt_name_prefix = color_info['code'], color_info['name']
            if pt_code_to_use:
                pt_path_format_str = self.frame_config.get("pt_path_format"); pt_bounds_config = self.frame_config.get("pt_bounds"); pt_path = None 
                if not pt_path_format_str: logger.error(f"PT Error: pt_path_format missing in frame_config for {self.frame_type}")
                else: pt_path = self.build_pt_frame_path(pt_code_to_use) 
                if pt_path and pt_bounds_config and "/error_path" not in pt_path: generated_frames.append({"name": f"{pt_name_prefix} Power/Toughness", "src": pt_path, "masks": [], "bounds": pt_bounds_config})
        is_legendary = 'Legendary' in type_line
        if self.legendary_crowns and is_legendary:
            primary_crown_color_code, secondary_crown_color_code = None, None; primary_crown_color_name, secondary_crown_color_name = "Legend", "Secondary"
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
                crown_src_path_format = self.frame_config.get("legend_crown_path_format_m15ub"); crown_bounds = self.frame_config.get("legend_crown_bounds"); crown_cover_src = self.frame_config.get("legend_crown_cover_src", "/img/black.png"); crown_cover_bounds = self.frame_config.get("legend_crown_cover_bounds")
                if crown_src_path_format and crown_bounds and crown_cover_src and crown_cover_bounds:
                    formatted_crown_path_secondary = self._format_path(crown_src_path_format, color_code_upper=secondary_crown_color_code.upper()) if secondary_crown_color_code else None
                    formatted_crown_path_primary = self._format_path(crown_src_path_format, color_code_upper=primary_crown_color_code.upper())
                    if secondary_crown_color_code and formatted_crown_path_secondary and "/error_path" not in formatted_crown_path_secondary: generated_frames.append({"name": f"{secondary_crown_color_name} Legend Crown", "src": formatted_crown_path_secondary, "masks": [{"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}], "bounds": crown_bounds})
                    if formatted_crown_path_primary and "/error_path" not in formatted_crown_path_primary: generated_frames.append({"name": f"{primary_crown_color_name} Legend Crown", "src": formatted_crown_path_primary, "masks": [], "bounds": crown_bounds}); generated_frames.append({"name": "Legend Crown Border Cover", "src": crown_cover_src, "masks": [], "bounds": crown_cover_bounds})
        main_frame_layers = []; base_frame_path_fmt = self.frame_config.get("frame_path_format"); land_frame_path_fmt = self.frame_config.get("land_frame_path_format"); mask_path_fmt = self.frame_config.get("mask_path_format")
        if not all([base_frame_path_fmt, land_frame_path_fmt, mask_path_fmt]): logger.error(f"M15UB MainFrame: Essential path formats missing for '{card_name_for_logging}'."); generated_frames.extend(main_frame_layers); return generated_frames
        pinline_mask_src = self._format_path(mask_path_fmt, mask_name="Pinline"); type_mask_src = self._format_path(mask_path_fmt, mask_name="Type"); title_mask_src = self._format_path(mask_path_fmt, mask_name="Title"); rules_mask_src = self._format_path(mask_path_fmt, mask_name="Rules")
        frame_mask_src = self.frame_config.get("frame_mask_name_for_main_frame_layer"); border_mask_src = self.frame_config.get("border_mask_name_for_main_frame_layer")
        primary_color_code_main, secondary_color_code_main = None, None; primary_color_name_main, secondary_color_name_main = "Unknown", None; ttfb_code, ttfb_name = None, None 
        base_codes = { k: COLOR_CODE_MAP.get(k, {}).get('code') for k in ['M', 'L', 'A', 'V', 'C'] }; base_names = { k: COLOR_CODE_MAP.get(k, {}).get('name') for k in ['M', 'L', 'A', 'V', 'C'] }
        if is_land_card and isinstance(color_info, list): 
            ttfb_code, ttfb_name = base_codes.get('L'), base_names.get('L', "Land")
            if len(color_info) > 1 and isinstance(color_info[1], dict) and 'code' in color_info[1]: 
                primary_color_code_main, primary_color_name_main = color_info[1]['code'], color_info[1]['name']
                if len(color_info) > 2 and isinstance(color_info[2], dict) and 'code' in color_info[2]: secondary_color_code_main, secondary_color_name_main = color_info[2]['code'], color_info[2]['name']
            elif len(color_info) == 1 and isinstance(color_info[0], dict) and 'code' in color_info[0]: primary_color_code_main, primary_color_name_main = color_info[0]['code'], color_info[0]['name']
            else: logger.warning(f"Unexpected land color_info for '{card_name_for_logging}'. Defaulting."); primary_color_code_main, primary_color_name_main = base_codes.get('L'), base_names.get('L', "Land")
        elif isinstance(color_info, dict):
            if color_info.get('is_gold'): comps = color_info.get('component_colors', []); primary_color_code_main, primary_color_name_main = (comps[0]['code'], comps[0]['name']) if comps else (None,None); secondary_color_code_main, secondary_color_name_main=(comps[1]['code'],comps[1]['name']) if len(comps)>1 else (None,None); ttfb_code,ttfb_name=base_codes.get('M'),base_names.get('M',"Multicolored")
            elif color_info.get('is_vehicle'): primary_color_code_main, primary_color_name_main = base_codes.get('V'), base_names.get('V', "Vehicle"); ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
            elif color_info.get('is_artifact'): primary_color_code_main, primary_color_name_main = base_codes.get('A'), base_names.get('A', "Artifact"); ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
            elif color_info.get('code') == base_codes.get('C'): primary_color_code_main, primary_color_name_main = base_codes.get('C'), base_names.get('C', "Colorless"); ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
            elif color_info.get('code'): primary_color_code_main, primary_color_name_main = color_info['code'], color_info['name']; ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main
        if not primary_color_code_main : logger.error(f"M15UB MainFrame: Primary color code MAIN missing for '{card_name_for_logging}'. color_info: {color_info}"); generated_frames.extend(main_frame_layers); return generated_frames
        if not ttfb_code: logger.warning(f"M15UB MainFrame: TTFB code missing for '{card_name_for_logging}', falling back to primary. color_info: {color_info}"); ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main 
        src_pinline_rules = ""; src_type_title = ""; src_frame_border = ""
        if is_land_card:
            if primary_color_code_main != base_codes.get('L'): src_pinline_rules = self._format_path(land_frame_path_fmt, color_code=primary_color_code_main); src_type_title = src_pinline_rules 
            else: src_pinline_rules = self._format_path(base_frame_path_fmt, color_code=primary_color_code_main); src_type_title = src_pinline_rules 
            src_frame_border = self._format_path(base_frame_path_fmt, color_code=base_codes.get('L')) 
        else: src_pinline_rules = self._format_path(base_frame_path_fmt, color_code=primary_color_code_main); src_type_title = self._format_path(base_frame_path_fmt, color_code=ttfb_code); src_frame_border = src_type_title 
        if "/error_path" in src_pinline_rules or "/error_path" in src_type_title or "/error_path" in src_frame_border : logger.error(f"M15UB MainFrame: Error in critical frame paths for '{card_name_for_logging}'."); generated_frames.extend(main_frame_layers); return generated_frames
        type_title_name_prefix = primary_color_name_main if (is_land_card and primary_color_code_main != base_codes.get('L')) else ttfb_name
        if secondary_color_code_main: 
            src_secondary_pinline_rules = self._format_path(land_frame_path_fmt if is_land_card else base_frame_path_fmt, color_code=secondary_color_code_main)
            if "/error_path" not in src_secondary_pinline_rules: main_frame_layers.extend([ {"name": f"{secondary_color_name_main} Frame", "src": src_secondary_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]}, {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": type_mask_src, "name": "Type"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": title_mask_src, "name": "Title"}]}, {"name": f"{secondary_color_name_main} Frame", "src": src_secondary_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]}, {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask_src, "name": "Frame"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask_src, "name": "Border"}]}])
            else: 
                logger.warning(f"M15UB MainFrame: Error generating secondary path for '{card_name_for_logging}'. Falling back to primary layers.")
                main_frame_layers.extend([ {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}]}, {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": type_mask_src, "name": "Type"}]}, {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": title_mask_src, "name": "Title"}]}, {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask_src, "name": "Frame"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask_src, "name": "Border"}]}])
        else: 
            main_frame_layers.extend([ {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": pinline_mask_src, "name": "Pinline"}]}, {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": type_mask_src, "name": "Type"}]}, {"name": f"{type_title_name_prefix} Frame", "src": src_type_title, "masks": [{"src": title_mask_src, "name": "Title"}]}, {"name": f"{primary_color_name_main} Frame", "src": src_pinline_rules, "masks": [{"src": rules_mask_src, "name": "Rules"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask_src, "name": "Frame"}]}, {"name": f"{ttfb_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask_src, "name": "Border"}]}])
        generated_frames.extend(main_frame_layers)
        return generated_frames
    # --- End of Pasted Frame Building Methods ---

    def build_card_data(self, card_name: str, card_data: Dict, color_info, 
                        is_basic_land_fetch_mode: bool = False,
                        basic_land_type_override: Optional[str] = None) -> Dict:
    
        logger.debug(f"build_card_data for '{card_name}', frame_type '{self.frame_type}'. Upscale Art: {self.upscale_art}, Auto-fit Art: {self.auto_fit_art}")
        
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
        
        rarity_from_scryfall = card_data.get('rarity', 'c')
        rarity_code_for_symbol = RARITY_MAP.get(rarity_from_scryfall, rarity_from_scryfall)

        art_crop_url = "" 
        if 'image_uris' in card_data and 'art_crop' in card_data['image_uris']: art_crop_url = card_data['image_uris']['art_crop']
        elif 'card_faces' in card_data and card_data['card_faces']:
            for face in card_data['card_faces']:
                if 'image_uris' in face and 'art_crop' in face['image_uris']: 
                    art_crop_url = face['image_uris']['art_crop']; break

        # --- (initializations as before for card name, set, etc.) ---
        
        # Art-related initializations
        art_x = self.frame_config.get("art_x", 0.0)
        art_y = self.frame_config.get("art_y", 0.0)
        art_zoom = self.frame_config.get("art_zoom", 1.0)
        art_rotate = self.frame_config.get("art_rotate", "0")
        
        final_art_source_url = art_crop_url # Default, may be overridden
        hosted_original_art_url: Optional[str] = None
        hosted_upscaled_art_url: Optional[str] = None
        original_art_bytes_for_pipeline: Optional[bytes] = None
        original_image_mime_type: Optional[str] = None
        
        # Determine initial best guess for original image extension from Scryfall URL
        # This will be updated if actual content is fetched and reveals a different extension.
        _, initial_ext_guess = os.path.splitext(art_crop_url.split('?')[0])
        if not initial_ext_guess or initial_ext_guess.lower() not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            initial_ext_guess = ".jpg" # Default fallback extension
        original_image_actual_ext: str = initial_ext_guess.lower()
        
        sanitized_card_name = sanitize_for_filename(scryfall_card_name)
        set_code_sanitized = sanitize_for_filename(set_code_from_scryfall)
        collector_number_sanitized = sanitize_for_filename(collector_number_from_scryfall)
        
        # --- 1. Auto-Fit Art (if enabled) ---
        if self.auto_fit_art and art_crop_url:
            logger.info(f"Auto-Fit: Starting for '{scryfall_card_name}' (Scryfall art URL: {art_crop_url})")
            art_source_for_dims_log = art_crop_url # For logging where bytes came from
        
            # A. Try to get original art bytes from Nginx for auto-fit
            # MODIFIED: This check now only runs in server mode
            if self.image_server_base_url:
                # Check with current best guess for extension, then try others if not found
                possible_extensions = [original_image_actual_ext] + [ext for ext in ['.jpg', '.png', '.jpeg', '.webp', '.gif'] if ext != original_image_actual_ext]
                for ext_try in possible_extensions:
                    base_filename_nginx_check = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{ext_try}"
                    potential_nginx_url = self._construct_nginx_public_url("original", base_filename_nginx_check)
                    if self._check_if_file_exists_on_server(potential_nginx_url):
                        logger.info(f"Auto-Fit: Found '{base_filename_nginx_check}' on Nginx. Fetching for dimensions.")
                        temp_bytes = self._fetch_image_bytes(potential_nginx_url, "Nginx original for auto-fit")
                        if temp_bytes:
                            original_art_bytes_for_pipeline = temp_bytes
                            hosted_original_art_url = potential_nginx_url
                            art_source_for_dims_log = potential_nginx_url
                            # Update actual extension and mime from the fetched Nginx content
                            mime, ext_from_content = self._get_image_mime_type_and_extension(original_art_bytes_for_pipeline)
                            if ext_from_content: original_image_actual_ext = ext_from_content
                            else: original_image_actual_ext = ext_try # Fallback to the extension that worked for HEAD
                            if mime: original_image_mime_type = mime
                            break # Found and fetched from Nginx
                        else:
                            logger.warning(f"Auto-Fit: Failed to fetch from Nginx {potential_nginx_url} despite HEAD check. Will try Scryfall if no other extension works.")
                    # else: not found with this extension, loop continues
                
            # B. If not found on Nginx, fetch from Scryfall for auto-fit
            if not original_art_bytes_for_pipeline and art_crop_url:
                logger.info(f"Auto-Fit: Fetching from Scryfall ({art_crop_url}) for dimensions.")
                temp_bytes = self._fetch_image_bytes(art_crop_url, "Scryfall original for auto-fit")
                if temp_bytes:
                    original_art_bytes_for_pipeline = temp_bytes
                    art_source_for_dims_log = f"Scryfall ({art_crop_url})"
                    # Update actual extension and mime from Scryfall content
                    mime, ext_from_content = self._get_image_mime_type_and_extension(original_art_bytes_for_pipeline)
                    if ext_from_content: original_image_actual_ext = ext_from_content
                    # original_image_mime_type will be set if ext_from_content is valid
                    if mime: original_image_mime_type = mime
                    
                    # C. If fetched from Scryfall and an output method is configured, save/host it
                    if self.output_dir or self.image_server_base_url:
                        # Use the now determined original_image_actual_ext for the filename
                        filename_to_host = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{original_image_actual_ext}"
                        
                        # MODIFIED: Use the new _save_image method
                        temp_hosted_url = self._save_image(original_art_bytes_for_pipeline, "original", filename_to_host)
                        
                        if temp_hosted_url:
                            hosted_original_art_url = temp_hosted_url
                        else:
                            logger.warning(f"Auto-Fit: Failed to save/host Scryfall-fetched art '{filename_to_host}'.")
                else:
                    logger.warning(f"Auto-Fit: Failed to fetch art from Scryfall ({art_crop_url}) for dimensions.")
        
            # D. Calculate auto-fit parameters if bytes were obtained
            if original_art_bytes_for_pipeline:
                auto_fit_params = self._calculate_auto_fit_art_params_from_data(original_art_bytes_for_pipeline, art_source_for_dims_log)
                if auto_fit_params:
                    art_x, art_y, art_zoom = auto_fit_params["artX"], auto_fit_params["artY"], auto_fit_params["artZoom"]
                    logger.info(f"Auto-Fit: Parameters applied for {scryfall_card_name} (from {art_source_for_dims_log}): X={art_x:.4f}, Y={art_y:.4f}, Zoom={art_zoom:.4f}")
                else:
                    logger.warning(f"Auto-Fit: Calculation failed using bytes from {art_source_for_dims_log}. Using default art parameters from config.")
            else:
                logger.warning(f"Auto-Fit: No art bytes obtained for {scryfall_card_name}. Using default art parameters from config.")
        
        # --- 2. Upscaling and Final Art Source Determination ---
        # MODIFIED: Check for either local or server output
        if self.upscale_art and art_crop_url and self.ilaria_upscaler_base_url and (self.image_server_base_url or self.output_dir):
            upscaler_model_sanitized = sanitize_for_filename(self.upscaler_model_name)

            # NEW: Construct directory name including both model and outscale factor
            # self.upscaler_outscale_factor is ensured to be >= 1 by the constructor
            upscaled_directory_name = f"{upscaler_model_sanitized}-{self.upscaler_outscale_factor}x"
            logger.info(f"Upscaling: Using subdirectory '{upscaled_directory_name}' for upscaled images.")

            # RealESRGAN typically outputs PNG, but we verify after generation
            upscaled_image_check_ext = ".png" 
            base_art_filename_upscaled_check = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{upscaled_image_check_ext}"

            # MODIFIED: Use the new upscaled_directory_name
            # In server mode, this is a full URL. In local mode, this will be a relative path.
            expected_upscaled_path = self._construct_nginx_public_url(upscaled_directory_name, base_art_filename_upscaled_check) if self.image_server_base_url else f"/{self.image_server_path_prefix.strip('/')}/{upscaled_directory_name}/{base_art_filename_upscaled_check}"
            
            used_existing_upscaled = False
            # A. Check if upscaled version already exists
            # MODIFIED: Check either server or local file
            if (self.image_server_base_url and self._check_if_file_exists_on_server(expected_upscaled_path)) or \
               (self.output_dir and (Path(self.output_dir) / expected_upscaled_path.lstrip('/')).exists()):
                logger.info(f"Upscaling: Found existing upscaled art '{base_art_filename_upscaled_check}' in subdir '{upscaled_directory_name}'.")
                hosted_upscaled_art_url = expected_upscaled_path
                final_art_source_url = hosted_upscaled_art_url
                used_existing_upscaled = True
                if self.upscaler_outscale_factor > 0:
                    art_zoom = art_zoom / self.upscaler_outscale_factor
                    logger.info(f"Upscaling: Adjusted artZoom for existing upscaled image to: {art_zoom:.4f} (factor: {self.upscaler_outscale_factor})")
                
                # If we used existing upscaled, ensure hosted_original_art_url is also known if original exists
                if not hosted_original_art_url: 
                    original_filename_check = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{original_image_actual_ext}"
                    potential_original_path = self._construct_nginx_public_url("original", original_filename_check) if self.image_server_base_url else f"/{self.image_server_path_prefix.strip('/')}/original/{original_filename_check}"
                    if (self.image_server_base_url and self._check_if_file_exists_on_server(potential_original_path)) or \
                       (self.output_dir and (Path(self.output_dir) / potential_original_path.lstrip('/')).exists()):
                        hosted_original_art_url = potential_original_path
        
            # B. If not using existing upscaled, proceed with upscaling pipeline
            if not used_existing_upscaled:
                # B1. Ensure original art bytes are available (if auto-fit was off or failed to get them)
                if not original_art_bytes_for_pipeline:
                    logger.info(f"Upscaling: Original art bytes not available from auto-fit step. Fetching now.")
                    # Try Nginx first (similar logic to auto-fit's Nginx check)
                    if self.image_server_base_url:
                        possible_extensions = [original_image_actual_ext] + [ext for ext in ['.jpg', '.png', '.jpeg', '.webp', '.gif'] if ext != original_image_actual_ext]
                        for ext_try in possible_extensions:
                            base_filename_nginx_check = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{ext_try}"
                            potential_nginx_url = self._construct_nginx_public_url("original", base_filename_nginx_check)
                            if self._check_if_file_exists_on_server(potential_nginx_url):
                                temp_bytes = self._fetch_image_bytes(potential_nginx_url, "Nginx original for upscaling pipeline")
                                if temp_bytes:
                                    original_art_bytes_for_pipeline = temp_bytes
                                    hosted_original_art_url = potential_nginx_url
                                    mime, ext_from_content = self._get_image_mime_type_and_extension(original_art_bytes_for_pipeline)
                                    if ext_from_content: original_image_actual_ext = ext_from_content
                                    else: original_image_actual_ext = ext_try
                                    if mime: original_image_mime_type = mime
                                    break # Found and fetched
                    # If not from Nginx, try Scryfall
                    if not original_art_bytes_for_pipeline and art_crop_url:
                        temp_bytes = self._fetch_image_bytes(art_crop_url, "Scryfall original for upscaling pipeline")
                        if temp_bytes:
                            original_art_bytes_for_pipeline = temp_bytes
                            mime, ext_from_content = self._get_image_mime_type_and_extension(original_art_bytes_for_pipeline)
                            if ext_from_content: original_image_actual_ext = ext_from_content
                            if mime: original_image_mime_type = mime
                
                # B2. Ensure original art is saved/hosted if bytes are available
                if original_art_bytes_for_pipeline and not hosted_original_art_url:
                    filename_to_host = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{original_image_actual_ext}"
                    
                    # MODIFIED: Use the new _save_image method
                    temp_hosted_url = self._save_image(original_art_bytes_for_pipeline, "original", filename_to_host)
                    if temp_hosted_url: hosted_original_art_url = temp_hosted_url
                
                # B3. Perform upscaling if original is available (hosted or local)
                if hosted_original_art_url:
                    # Ensure mime type is known for Ilaria (should be if bytes were processed)
                    if not original_image_mime_type and original_art_bytes_for_pipeline:
                         original_image_mime_type, _ = self._get_image_mime_type_and_extension(original_art_bytes_for_pipeline)
        
                    if original_image_mime_type: # Ilaria API requires mime_type in its payload
                        filename_of_hosted_original = hosted_original_art_url.split('/')[-1] # For Ilaria's 'orig_name'
                        upscaled_image_bytes = self._upscale_image_with_ilaria(hosted_original_art_url, filename_of_hosted_original, original_image_mime_type)
                        if upscaled_image_bytes:
                            _, actual_upscaled_ext = self._get_image_mime_type_and_extension(upscaled_image_bytes)
                            if not actual_upscaled_ext: actual_upscaled_ext = upscaled_image_check_ext # Default if detection fails
                            if not actual_upscaled_ext.startswith('.'): actual_upscaled_ext = '.' + actual_upscaled_ext
                            upscaled_art_filename_to_save = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{actual_upscaled_ext}"
                            
                            # MODIFIED: Use the new _save_image method
                            logger.info(f"Upscaling: Saving/Hosting upscaled art '{upscaled_art_filename_to_save}' in subdir '{upscaled_directory_name}'.")
                            temp_hosted_upscaled_url = self._save_image(upscaled_image_bytes, upscaled_directory_name, upscaled_art_filename_to_save)
                            
                            if temp_hosted_upscaled_url:
                                hosted_upscaled_art_url = temp_hosted_upscaled_url
                                final_art_source_url = hosted_upscaled_art_url
                                if self.upscaler_outscale_factor > 0:
                                    art_zoom = art_zoom / self.upscaler_outscale_factor
                                    logger.info(f"Upscaling: Adjusted artZoom for newly upscaled image to: {art_zoom:.4f}")
                            else: # Failed to host upscaled image
                                logger.warning(f"Upscaling: Failed to save/host upscaled art. Falling back to original art if available.")
                                if hosted_original_art_url: final_art_source_url = hosted_original_art_url
                                # else final_art_source_url remains Scryfall URL (art_crop_url) by default
                        else: # Upscaling itself failed
                            logger.warning(f"Upscaling: Ilaria upscaling failed for {filename_of_hosted_original}. Falling back to original art if available.")
                            if hosted_original_art_url: final_art_source_url = hosted_original_art_url
                    else: # Mime type for original couldn't be determined for Ilaria
                        logger.warning(f"Upscaling: Cannot determine MIME type for original art {hosted_original_art_url}. Skipping upscale. Falling back to original art if available.")
                        if hosted_original_art_url: final_art_source_url = hosted_original_art_url
                elif original_art_bytes_for_pipeline : # Have bytes for original, but it's not hosted (e.g., Nginx not configured or hosting failed)
                    logger.warning(f"Upscaling: Original art available but not saved/hosted. Cannot upscale. Using Scryfall URL as art source.")
                    # final_art_source_url remains art_crop_url
                else: # No original art bytes could be obtained at all
                    logger.warning(f"Upscaling: Cannot obtain or host original art. Cannot upscale. Using Scryfall URL as art source.")
                    # final_art_source_url remains art_crop_url
        
        elif hosted_original_art_url: 
            # Upscaling not enabled/configured, but auto-fit (or other prior logic) might have successfully saved/hosted an original.
            # If so, use that as the final source.
            final_art_source_url = hosted_original_art_url
        # else: Upscaling not enabled, no local/hosted original found/created, so final_art_source_url remains art_crop_url (the initial default)

        set_symbol_x = self.frame_config.get("set_symbol_x", 0.0); set_symbol_y = self.frame_config.get("set_symbol_y", 0.0); set_symbol_zoom = self.frame_config.get("set_symbol_zoom", 0.1)
        actual_set_code_for_url = self.set_symbol_override.lower() if self.set_symbol_override else set_code_from_scryfall.lower()
        set_symbol_source_url = f"{ccProto}://{ccHost}:{ccPort}/img/setSymbols/official/{actual_set_code_for_url}-{rarity_code_for_symbol}.svg"
        if self.auto_fit_set_symbol and set_symbol_source_url:
            auto_fit_symbol_params_result = self._calculate_auto_fit_set_symbol_params(set_symbol_source_url)
            if auto_fit_symbol_params_result:
                status = auto_fit_symbol_params_result.get("_status")
                if status in ["success_lookup", "success_calculated"]:
                    set_symbol_x = auto_fit_symbol_params_result["setSymbolX"]; set_symbol_y = auto_fit_symbol_params_result["setSymbolY"]; set_symbol_zoom = auto_fit_symbol_params_result["setSymbolZoom"]
                elif status == "calculation_issue_default_fallback": logger.warning(f"Auto-fit for set symbol {set_symbol_source_url} resulted in fallback.")
            else: logger.warning(f"Auto-fit set symbol calculation returned None for {set_symbol_source_url}.")

        power_val = card_data.get('power', ''); toughness_val = card_data.get('toughness', ''); pt_text_final = ""
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
            "artX": art_x, "artY": art_y, "artZoom": art_zoom, 
            "artRotate": art_rotate,
            "artSourceOriginalScryfall": art_crop_url, 
            **({"artSourceHostedOriginal": hosted_original_art_url} if hosted_original_art_url else {}),
            **({"artSourceHostedUpscaled": hosted_upscaled_art_url} if hosted_upscaled_art_url and hosted_upscaled_art_url != final_art_source_url else {}),
            "setSymbolSource": set_symbol_source_url, "setSymbolX":set_symbol_x, "setSymbolY": set_symbol_y, "setSymbolZoom": set_symbol_zoom,
            "watermarkSource": f"{ccProto}://{ccHost}:{ccPort}/{self.frame_config['watermark_source']}",
            "watermarkX": self.frame_config["watermark_x"], "watermarkY": self.frame_config["watermark_y"], "watermarkZoom": self.frame_config["watermark_zoom"], 
            "watermarkLeft": self.frame_config["watermark_left"], "watermarkRight": self.frame_config["watermark_right"], "watermarkOpacity": self.frame_config["watermark_opacity"],
            "version": self.frame_config.get("version_string", self.frame_type), 
            "showsFlavorBar": shows_flavor_bar_for_this_card, 
            "manaSymbols": mana_symbols,
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
