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
from pathlib import Path

import requests 
from PIL import Image 
from lxml import etree 

from config import (
    ccProto, ccHost, ccPort,
    DEFAULT_INFO_YEAR, DEFAULT_INFO_RARITY, DEFAULT_INFO_SET,
    DEFAULT_INFO_LANGUAGE, DEFAULT_INFO_ARTIST, DEFAULT_INFO_NOTE, DEFAULT_INFO_NUMBER
)
from color_mapping import COLOR_CODE_MAP, RARITY_MAP
from exceptions import ScryfallAPIException, FrameGenerationException, DataProcessingException, ImageProcessingException

from gradio_client import Client, file as gradio_file

logger = logging.getLogger(__name__)

def calculate_font_size(text: str, box_width: float, box_height: float, initial_size: float, aspect_ratio: float = 0.5):
    size = initial_size
    while size > 0.001:
        # Estimate the number of characters that can fit on a line
        chars_per_line = box_width / (size * aspect_ratio)
        if chars_per_line == 0:
            return 0.001 # Avoid division by zero
        # Estimate the number of lines
        num_lines = sum([len(line) / chars_per_line for line in text.split('\n')])
        # Estimate the height of the text
        text_height = num_lines * size
        if text_height <= box_height:
            return size
        size -= 0.001
    return size

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
                 image_server_path_prefix: str = "/local_art",
                 output_dir: Optional[str] = None,
                 upload_to_server: bool = False
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
        
        # --- MODIFIED: Correctly format the path prefix ---
        # Ensure it starts with a slash and does not end with one, for consistent joining.
        if image_server_path_prefix:
            self.image_server_path_prefix = "/" + image_server_path_prefix.strip("/")
        else:
            self.image_server_path_prefix = ""
        # --- END MODIFICATION ---
        
        self.output_dir = output_dir
        self.upload_to_server = upload_to_server

        if self.upscale_art and not self.ilaria_upscaler_base_url:
            logger.warning("Upscaling is enabled, but --ilaria_base_url is not configured. Upscaling will be skipped.")

        self.symbol_placement_lookup = {}
        if self.auto_fit_set_symbol: 
            try:
                with open("symbol_placements.json", "r") as f: self.symbol_placement_lookup = json.load(f)
                logger.info(f"Loaded {len(self.symbol_placement_lookup)} entries from symbol_placements.json")
            except FileNotFoundError:
                raise DataProcessingException("symbol_placements.json not found.", "Please create the file or disable auto_fit_set_symbol.")
            except json.JSONDecodeError as e:
                raise DataProcessingException(f"Error decoding symbol_placements.json: {e}", "Please check the file for syntax errors.")

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
            if not svg_dims or svg_dims["width"] <= 0 or svg_dims["height"] <= 0:
                raise DataProcessingException("Invalid SVG dimensions", f"Could not get valid dimensions from {set_symbol_url}")
            card_w, card_h = self.frame_config.get("width"), self.frame_config.get("height")
            bounds_cfg = self.frame_config.get("set_symbol_bounds")
            align_x, align_y = self.frame_config.get("set_symbol_align_x_right"), self.frame_config.get("set_symbol_align_y_center")
            if not (card_w and card_h and bounds_cfg and isinstance(bounds_cfg, dict) and all(k in bounds_cfg for k in ('x', 'y', 'width', 'height')) and align_x is not None and align_y is not None):
                raise FrameGenerationException("Frame config incomplete for set symbol auto-fit", f"Please check the frame config for {self.frame_type}")
            scale_x = (bounds_cfg["width"] * card_w) / svg_dims["width"]; scale_y = (bounds_cfg["height"] * card_h) / svg_dims["height"]
            zoom = min(scale_x, scale_y)
            if zoom <= 1e-6:
                raise DataProcessingException("Set symbol zoom too small", f"Calculated zoom for {set_symbol_url} is too small.")
            scaled_w_rel = (svg_dims["width"] * zoom) / card_w; scaled_h_rel = (svg_dims["height"] * zoom) / card_h
            return { "setSymbolX": align_x - scaled_w_rel, "setSymbolY": align_y - (scaled_h_rel / 2.0), "setSymbolZoom": zoom, "_status": "success_calculated" }
        except requests.RequestException as e:
            raise ScryfallAPIException(f"Symbol SVG request error for {set_symbol_url}", str(e))
        except Exception as e:
            raise DataProcessingException(f"Symbol auto-fit error for {set_symbol_url}", str(e))

    def _get_svg_dimensions(self, svg_content_bytes: bytes) -> Optional[Dict[str, float]]:
        if not svg_content_bytes: return None
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
            svg_root = etree.fromstring(svg_content_bytes, parser=parser)
            if svg_root is None or not svg_root.tag.endswith('svg'):
                raise DataProcessingException("Failed to parse SVG.", "The provided content is not a valid SVG.")
            viewbox, width_str, height_str = svg_root.get("viewBox"), svg_root.get("width"), svg_root.get("height")
            w, h = None, None
            if viewbox: 
                try: p = [float(x) for x in re.split(r'[,\\s]+', viewbox.strip())]; w, h = (p[2], p[3]) if len(p) == 4 else (None, None)
                except ValueError:
                    raise DataProcessingException(f"Could not parse viewBox: '{viewbox}'", "Please check the SVG file for errors.")
            if w is None and width_str and not width_str.endswith('%'): 
                try: w = float(re.sub(r'[^\d\\.\-e]', '', width_str))
                except ValueError:
                    raise DataProcessingException(f"Could not parse width: '{width_str}'", "Please check the SVG file for errors.")
            if h is None and height_str and not height_str.endswith('%'): 
                try: h = float(re.sub(r'[^\d\\.\-e]', '', height_str))
                except ValueError:
                    raise DataProcessingException(f"Could not parse height: '{height_str}'", "Please check the SVG file for errors.")
            return {"width": w, "height": h} if w and h and w > 0 and h > 0 else None
        except Exception as e:
            raise DataProcessingException(f"Error parsing SVG dimensions: {e}", "Please check the SVG file for errors.")

    def _calculate_auto_fit_art_params_from_data(self, image_bytes: bytes, log_ref: str) -> Optional[Dict[str, float]]:
        if not image_bytes:
            raise ImageProcessingException("No image bytes for art auto-fit", f"No image data provided for {log_ref}")
        try:
            img = Image.open(io.BytesIO(image_bytes)); w, h = img.width, img.height; img.close()
            if w == 0 or h == 0:
                raise ImageProcessingException("Zero dimensions for art", f"Image dimensions for {log_ref} are zero.")
            cfg = self.frame_config; card_w, card_h = cfg.get("width"), cfg.get("height")
            b = cfg.get("art_bounds")
            if not (card_w and card_h and b and isinstance(b, dict) and all(k in b for k in ('x', 'y', 'width', 'height'))):
                raise FrameGenerationException("Incomplete config for art auto-fit", f"Please check the frame config for {self.frame_type}")
            if b["width"] <= 0 or b["height"] <= 0:
                raise FrameGenerationException("Invalid art_bounds for auto-fit", f"Please check the frame config for {self.frame_type}")
            abs_w, abs_h = b["width"] * card_w, b["height"] * card_h
            zoom = max(abs_w / w, abs_h / h)
            if zoom <= 1e-6:
                raise ImageProcessingException("Art zoom too small for auto-fit", f"Calculated zoom for {log_ref} is too small.")
            return {"artX": b["x"] + (abs_w - w * zoom) / 2 / card_w, "artY": b["y"] + (abs_h - h * zoom) / 2 / card_h, "artZoom": zoom}
        except Exception as e:
            raise ImageProcessingException(f"Art auto-fit from data error for {log_ref}", str(e))

    def _calculate_auto_fit_art_params(self, art_url: str) -> Optional[Dict[str, float]]: # From baseline
        if not art_url: return None
        try:
            logger.debug(f"Auto-fit: Fetching Scryfall art from {art_url} for dimension calculation.")
            response = requests.get(art_url, timeout=10); response.raise_for_status()
            if self.api_delay_seconds > 0 and (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True):
                time.sleep(self.api_delay_seconds)
            return self._calculate_auto_fit_art_params_from_data(response.content, art_url)
        except requests.RequestException as e:
            raise ScryfallAPIException(f"Error in _calculate_auto_fit_art_params for {art_url}", str(e))
        except Exception as e:
            raise ImageProcessingException(f"Error in _calculate_auto_fit_art_params for {art_url}", str(e))

    def _fetch_image_bytes(self, url: str, purpose: str = "generic") -> Optional[bytes]: # General helper
        if not url: return None
        try:
            logger.debug(f"Fetching image for {purpose} from: {url}")
            response = requests.get(url, timeout=10); response.raise_for_status()
            if "scryfall.com" in url.lower() and self.api_delay_seconds > 0 and \
               (not hasattr(response, 'from_cache') or response.from_cache is False if hasattr(response, 'from_cache') else True):
                time.sleep(self.api_delay_seconds)
            return response.content
        except requests.RequestException as e:
            raise ImageProcessingException(f"Failed to fetch image for {purpose} from {url}", str(e))
            
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

    def _upscale_image_with_ilaria(self, original_art_url_or_path: str, filename: str, mime: Optional[str]) -> Optional[bytes]:
        if not self.ilaria_upscaler_base_url:
            raise ImageProcessingException("Ilaria URL not set.", "Please configure the --ilaria_base_url argument.")
        if not original_art_url_or_path:
            raise ImageProcessingException(f"No original art URL or path for '{filename}'.", "Cannot upscale without a source image.")

        img_bytes = None
        if self.output_dir:
            local_path = Path(self.output_dir) / original_art_url_or_path.lstrip('/')
            logger.debug(f"Upscaling: Reading original image from local path: {local_path}")
            try:
                with open(local_path, "rb") as f:
                    img_bytes = f.read()
            except FileNotFoundError:
                raise ImageProcessingException(f"Upscaling failed: Original image not found at local path {local_path}", "Please ensure the original image exists.")
            except Exception as e:
                raise ImageProcessingException(f"Upscaling failed: Could not read local file {local_path}", str(e))
        else:
            img_bytes = self._fetch_image_bytes(original_art_url_or_path, "Upscaling with gradio_client")

        if not img_bytes:
            raise ImageProcessingException(f"Failed to get image bytes from {original_art_url_or_path}", "Cannot upscale without image data.")

        try:
            logger.info(f"Connecting to Ilaria Upscaler via gradio_client.")
            client = Client(self.ilaria_upscaler_base_url)

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
                result_path = result[0]
            else:
                result_path = result

            logger.info(f"Upscaled image path: {result_path}")

            with open(result_path, "rb") as f:
                return f.read()

        except Exception as e:
            raise ImageProcessingException(f"Gradio upscaling error for '{filename}'", str(e))

    def _check_if_file_exists_on_server(self, public_url: str) -> bool:
        if not public_url: return False
        try:
            r = requests.head(public_url, timeout=15, allow_redirects=True) 
            if r.status_code == 200: logger.info(f"Exists: {public_url}"); return True
            if r.status_code == 404: logger.info(f"Not found: {public_url}"); return False
            logger.warning(f"Status {r.status_code} checking {public_url}. Assuming not existent."); return False 
        except Exception as e: logger.warning(f"Error checking {public_url}: {e}. Assuming not existent."); return False

    def _output_image(self, img_bytes: bytes, sub_dir: str, filename: str):
        if not img_bytes:
            raise ImageProcessingException(f"No image bytes provided for '{filename}' in '{sub_dir}'.", "Cannot save empty image.")

        if self.output_dir:
            try:
                local_save_dir = Path(self.output_dir) / self.image_server_path_prefix.strip('/') / sub_dir.strip('/')
                local_save_dir.mkdir(parents=True, exist_ok=True)
                local_file_path = local_save_dir / filename
                with open(local_file_path, 'wb') as f:
                    f.write(img_bytes)
                logger.info(f"Saved image locally to: {local_file_path}")
            except Exception as e:
                raise ImageProcessingException(f"Local save error for '{filename}'", str(e))

        elif self.upload_to_server:
            if not self.image_server_base_url:
                raise ImageProcessingException(f"Cannot upload '{filename}': --upload-to-server is set, but --image-server-base-url is not.", "Please configure the --image-server-base-url argument.")
            
            upload_url = f"{self.image_server_base_url.rstrip('/')}{self.image_server_path_prefix}/{sub_dir.strip('/')}/{filename}"
            
            logger.info(f"Uploading '{filename}' to: {upload_url}")
            mime, _ = self._get_image_mime_type_and_extension(img_bytes)
            headers = {'Content-Type': mime if mime else 'application/octet-stream'}
            try:
                r = requests.put(upload_url, data=img_bytes, headers=headers, timeout=60)
                r.raise_for_status()
                logger.info(f"Successfully uploaded '{filename}'.")
            except Exception as e:
                raise ImageProcessingException(f"Upload error for '{filename}'", str(e))
    
    # ... (rest of the file is unchanged) ...
    def _format_path(self, path_format_str: Optional[str], **kwargs) -> str: # From baseline
        if not path_format_str:
            if not ('pt_path_format' in str(kwargs.get('caller_description', '')) and kwargs.get('path_type_optional', False)):
                 raise FrameGenerationException("Path format string is None or empty.", f"Args: {kwargs}")
            return "/img/error_path.png" 
        valid_args = {k: v for k, v in kwargs.items() if f"{{{k}}}" in path_format_str}
        try: return path_format_str.format(**valid_args)
        except KeyError as e:
            raise FrameGenerationException(f"KeyError formatting path '{path_format_str}'", f"Args: {valid_args}, Error: {e}")
        except Exception as e_gen:
            raise FrameGenerationException(f"Generic error formatting path '{path_format_str}'", f"Args: {valid_args}, Error: {e_gen}")

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

        raise FrameGenerationException(f"Could not determine land frame path for {self.frame_type} with color {color_code}", "Please check the frame config.")

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
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]}]
                )
            elif len(color_info) > 1: 
                mana_color = color_info[1]
                main_frame_layers.extend([
                    {"name": f"{mana_color['name']} Land Frame", "src": self.build_land_frame_path(mana_color['code']), "masks": [{"src": self.build_mask_path("pinline"), "name": "Pinline"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("type"), "name": "Type"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("title"), "name": "Title"}]},
                    {"name": f"{mana_color['name']} Land Frame", "src": self.build_land_frame_path(mana_color['code']), "masks": [{"src": self.build_mask_path("rules"), "name": "Rules"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("frame"), "name": "Frame"}]},
                    {"name": "Land Frame", "src": base_src, "masks": [{"src": self.build_mask_path("border"), "name": "Border"}]}]
                )
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
                if not pt_path_format_str:
                    raise FrameGenerationException(f"PT Error: pt_path_format missing in frame_config for {self.frame_type}", "Please check the frame config.")
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
        if not all([base_frame_path_fmt, land_frame_path_fmt, mask_path_fmt]):
            raise FrameGenerationException(f"M15UB MainFrame: Essential path formats missing for '{card_name_for_logging}'.", "Please check the frame config.")
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
        if not primary_color_code_main :
            raise FrameGenerationException(f"M15UB MainFrame: Primary color code MAIN missing for '{card_name_for_logging}'.", f"color_info: {color_info}")
        if not ttfb_code: logger.warning(f"M15UB MainFrame: TTFB code missing for '{card_name_for_logging}', falling back to primary. color_info: {color_info}"); ttfb_code, ttfb_name = primary_color_code_main, primary_color_name_main 
        src_pinline_rules = ""; src_type_title = ""; src_frame_border = ""
        if is_land_card:
            if primary_color_code_main != base_codes.get('L'): src_pinline_rules = self._format_path(land_frame_path_fmt, color_code=primary_color_code_main); src_type_title = src_pinline_rules 
            else: src_pinline_rules = self._format_path(base_frame_path_fmt, color_code=primary_color_code_main); src_type_title = src_pinline_rules 
            src_frame_border = self._format_path(base_frame_path_fmt, color_code=base_codes.get('L')) 
        else: src_pinline_rules = self._format_path(base_frame_path_fmt, color_code=primary_color_code_main); src_type_title = self._format_path(base_frame_path_fmt, color_code=ttfb_code); src_frame_border = src_type_title 
        if "/error_path" in src_pinline_rules or "/error_path" in src_type_title or "/error_path" in src_frame_border :
            raise FrameGenerationException(f"M15UB MainFrame: Error in critical frame paths for '{card_name_for_logging}'.", "Please check the frame config.")
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

    def build_modern_frames(self, color_info: Union[Dict, List], card_data: Dict) -> List[Dict]:

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

                            generated_frames.append({"name": f"{secondary_crown_color_name} Legend Crown", "src": self._format_path(crown_path_format, color_code=secondary_crown_color_code.lower()), "masks": [{"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}], "bounds": crown_bounds})

                        generated_frames.append({"name": f"{primary_crown_color_name} Legend Crown", "src": self._format_path(crown_path_format, color_code=primary_crown_color_code.lower()), "masks": [], "bounds": crown_bounds})

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

            land_frame_path_fmt = self.frame_config.get("land_frame_path_format")

            main_frame_mask_src = self.frame_config.get("frame_mask_name_for_main_frame_layer"); main_border_mask_src = self.frame_config.get("border_mask_name_for_main_frame_layer")

            if not all([base_frame_path_fmt, mask_path_fmt, main_frame_mask_src, main_border_mask_src]): return generated_frames

    

            primary_color_code, primary_color_name = None, "Unknown"; secondary_color_code, secondary_color_name = None, None

            base_multicolor_code = COLOR_CODE_MAP['M']['code']; base_multicolor_name = COLOR_CODE_MAP['M']['name']

            ttfb_code, ttfb_name = None, None 

                        is_land = isinstance(color_info, list)

                        if is_land:

                            ttfb_frame_code, ttfb_frame_name = COLOR_CODE_MAP['L']['code'], COLOR_CODE_MAP['L']['name']

                            if len(color_info) > 1 and color_info[1]['code'] == 'm':

                                ttfb_code, ttfb_name = COLOR_CODE_MAP['M']['code'], COLOR_CODE_MAP['M']['name']

                            else:

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

                        pinline_mask = self._format_path(mask_path_fmt, mask_name="pinline")

                        type_mask = self._format_path(mask_path_fmt, mask_name="type")

                        title_mask = self._format_path(mask_path_fmt, mask_name="title")

                        rules_mask = self._format_path(mask_path_fmt, mask_name="rules")

                        frame_mask = self._format_path(mask_path_fmt, mask_name="frame")

                        border_mask = self._format_path(mask_path_fmt, mask_name="border")

            

                        if is_land:

                            src_frame_border = self._format_path(base_frame_path_fmt, color_code=ttfb_frame_code)

                            if secondary_color_code and land_frame_path_fmt:

                                src_land_secondary = self._format_path(land_frame_path_fmt, color_code=secondary_color_code)

                                src_land_primary = self._format_path(land_frame_path_fmt, color_code=primary_color_code)

                                main_frame_layers.extend([

                                    {"name": f"{secondary_color_name} Land Frame", "src": src_land_secondary, "masks": [{"src": pinline_mask, "name": "Pinline"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},

                                    {"name": f"{primary_color_name} Land Frame", "src": src_land_primary, "masks": [{"src": pinline_mask, "name": "Pinline"}]},

                                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": type_mask, "name": "Type"}]},

                                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": title_mask, "name": "Title"}]},

                                    {"name": f"{secondary_color_name} Land Frame", "src": src_land_secondary, "masks": [{"src": rules_mask, "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},

                                    {"name": f"{primary_color_name} Land Frame", "src": src_land_primary, "masks": [{"src": rules_mask, "name": "Rules"}]},

                                    {"name": f"{ttfb_frame_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask, "name": "Frame"}]},

                                    {"name": f"{ttfb_frame_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask, "name": "Border"}]}]

                                )

                            elif primary_color_code and land_frame_path_fmt:

                                src_land_primary = self._format_path(land_frame_path_fmt, color_code=primary_color_code)

                                main_frame_layers.extend([

                                    {"name": f"{primary_color_name} Land Frame", "src": src_land_primary, "masks": [{"src": pinline_mask, "name": "Pinline"}]},

                                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": type_mask, "name": "Type"}]},

                                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": title_mask, "name": "Title"}]},

                                    {"name": f"{primary_color_name} Land Frame", "src": src_land_primary, "masks": [{"src": rules_mask, "name": "Rules"}]},

                                    {"name": f"{ttfb_frame_name} Frame", "src": src_frame_border, "masks": [{"src": frame_mask, "name": "Frame"}]},

                                    {"name": f"{ttfb_frame_name} Frame", "src": src_frame_border, "masks": [{"src": border_mask, "name": "Border"}]}]

                                )

                else:

                    main_frame_layers.extend([

                        {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": pinline_mask, "name": "Pinline"}]},

                        {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": type_mask, "name": "Type"}]},

                        {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": title_mask, "name": "Title"}]},

                        {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": rules_mask, "name": "Rules"}]},

                        {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": frame_mask, "name": "Frame"}]},

                        {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": border_mask, "name": "Border"}]}]

                    )

            elif secondary_color_code and src_secondary and "/error_path" not in src_secondary: 

                main_frame_layers.extend([

                    {"name": f"{secondary_color_name} Frame", "src": src_secondary, "masks": [{"src": pinline_mask, "name": "Pinline"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},

                    {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": pinline_mask, "name": "Pinline"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": type_mask, "name": "Type"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": title_mask, "name": "Title"}]},

                    {"name": f"{secondary_color_name} Frame", "src": src_secondary, "masks": [{"src": rules_mask, "name": "Rules"}, {"src": "/img/frames/maskRightHalf.png", "name": "Right Half"}]},

                    {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": rules_mask, "name": "Rules"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": frame_mask, "name": "Frame"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": border_mask, "name": "Border"}]}]

                )

            else: 

                main_frame_layers.extend([

                    {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": pinline_mask, "name": "Pinline"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": type_mask, "name": "Type"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": title_mask, "name": "Title"}]},

                    {"name": f"{primary_color_name} Frame", "src": src_primary, "masks": [{"src": rules_mask, "name": "Rules"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": frame_mask, "name": "Frame"}]},

                    {"name": f"{ttfb_name} Frame", "src": src_ttfb, "masks": [{"src": border_mask, "name": "Border"}]}]

                )

            generated_frames.extend(main_frame_layers)

            return generated_frames

    
    def build_card_data(self, card_name: str, card_data: Dict, color_info,
                            is_basic_land_fetch_mode: bool = False,
                            basic_land_type_override: Optional[str] = None) -> Dict:
        
            logger.debug(f"build_card_data for '{card_name}', frame_type '{self.frame_type}'. Upscale Art: {self.upscale_art}, Auto-fit Art: {self.auto_fit_art}")
            
            frames_for_card_obj = []
            if self.frame_type == "8th": frames_for_card_obj = self.build_eighth_edition_frames(color_info, card_data)
            elif self.frame_type == "m15": frames_for_card_obj = self.build_m15_frames(color_info, card_data)
            elif self.frame_type == "m15ub": frames_for_card_obj = self.build_m15ub_frames(color_info, card_data)
            elif self.frame_type == "modern": frames_for_card_obj = self.build_modern_frames(color_info, card_data)
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
            if not art_crop_url:
                raise DataProcessingException("Missing art_crop URL", f"No art_crop URL found for {scryfall_card_name}")
            art_x = self.frame_config.get("art_x", 0.0)
            art_y = self.frame_config.get("art_y", 0.0)
            art_zoom = self.frame_config.get("art_zoom", 1.0)
            art_rotate = self.frame_config.get("art_rotate", "0")
            
            final_art_source_url = art_crop_url
            hosted_original_art_url: Optional[str] = None
            hosted_upscaled_art_url: Optional[str] = None
            original_art_bytes_for_pipeline: Optional[bytes] = None
            original_image_mime_type: Optional[str] = None
            
            _, initial_ext_guess = os.path.splitext(art_crop_url.split('?')[0])
            if not initial_ext_guess or initial_ext_guess.lower() not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                initial_ext_guess = ".jpg"
            original_image_actual_ext: str = initial_ext_guess.lower()
            
            sanitized_card_name = sanitize_for_filename(scryfall_card_name)
            set_code_sanitized = sanitize_for_filename(set_code_from_scryfall)
            collector_number_sanitized = sanitize_for_filename(collector_number_from_scryfall)
            
            # --- Art Processing Pipeline ---
            # Only run if an output action is specified
            if self.output_dir or self.upload_to_server:
                # 1. Get original art bytes (from server or Scryfall)
                if self.upload_to_server:
                    possible_extensions = [original_image_actual_ext] + [ext for ext in ['.jpg', '.png', '.jpeg', '.webp', '.gif'] if ext != original_image_actual_ext]
                    for ext_try in possible_extensions:
                        base_filename_check = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{ext_try}"
                        potential_url = f"{self.image_server_base_url.rstrip('/')}{self.image_server_path_prefix}/original/{base_filename_check}"
                        if self._check_if_file_exists_on_server(potential_url):
                            temp_bytes = self._fetch_image_bytes(potential_url, "server original")
                            if temp_bytes:
                                original_art_bytes_for_pipeline = temp_bytes
                                hosted_original_art_url = potential_url
                                mime, ext = self._get_image_mime_type_and_extension(temp_bytes)
                                if ext: original_image_actual_ext = ext
                                if mime: original_image_mime_type = mime
                                break
                
                if not original_art_bytes_for_pipeline and art_crop_url:
                    original_art_bytes_for_pipeline = self._fetch_image_bytes(art_crop_url, "Scryfall original")
                    if original_art_bytes_for_pipeline:
                        mime, ext = self._get_image_mime_type_and_extension(original_art_bytes_for_pipeline)
                        if ext: original_image_actual_ext = ext
                        if mime: original_image_mime_type = mime
    
                # 2. If we have bytes, save/upload the original and calculate auto-fit
                if original_art_bytes_for_pipeline:
                    if not hosted_original_art_url:
                        filename_to_output = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{original_image_actual_ext}"
                        self._output_image(original_art_bytes_for_pipeline, "original", filename_to_output)
                        hosted_original_art_url = f"{self.image_server_base_url.rstrip('/')}{self.image_server_path_prefix}/original/{filename_to_output}"
    
                    if self.auto_fit_art:
                        auto_fit_params = self._calculate_auto_fit_art_params_from_data(original_art_bytes_for_pipeline, hosted_original_art_url)
                        if auto_fit_params:
                            art_x, art_y, art_zoom = auto_fit_params["artX"], auto_fit_params["artY"], auto_fit_params["artZoom"]
                            logger.info(f"Auto-Fit applied for {scryfall_card_name}: X={art_x:.4f}, Y={art_y:.4f}, Zoom={art_zoom:.4f}")
    
                # 3. Upscale if requested
                if self.upscale_art and original_art_bytes_for_pipeline and self.ilaria_upscaler_base_url:
                    upscaled_dir = f"{sanitized_card_name(self.upscaler_model_name)}-{self.upscaler_outscale_factor}x"
                    upscaled_filename_check = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}.png"
                    expected_upscaled_url = f"{self.image_server_base_url.rstrip('/')}{self.image_server_path_prefix}/{upscaled_dir}/{upscaled_filename_check}"
                    
                    # Check if upscaled version already exists
                    if (self.upload_to_server and self._check_if_file_exists_on_server(expected_upscaled_url)) or \
                       (self.output_dir and (Path(self.output_dir) / self.image_server_path_prefix.strip('/') / upscaled_dir / upscaled_filename_check).exists()):
                        logger.info(f"Found existing upscaled art for '{scryfall_card_name}'.")
                        hosted_upscaled_art_url = expected_upscaled_url
                    else:
                        # Determine the path/URL to the original art for the upscaler
                        original_art_path_for_upscaler = f"{self.image_server_path_prefix}/original/{hosted_original_art_url.split('/')[-1]}" if self.output_dir else hosted_original_art_url
                        
                        upscaled_bytes = self._upscale_image_with_ilaria(original_art_path_for_upscaler, hosted_original_art_url.split('/')[-1], original_image_mime_type)
                        if upscaled_bytes:
                            _, upscaled_ext = self._get_image_mime_type_and_extension(upscaled_bytes)
                            upscaled_filename = f"{sanitized_card_name}_{set_code_sanitized}_{collector_number_sanitized}{upscaled_ext or '.png'}"
                            self._output_image(upscaled_bytes, upscaled_dir, upscaled_filename)
                            hosted_upscaled_art_url = f"{self.image_server_base_url.rstrip('/')}{self.image_server_path_prefix}/{upscaled_dir}/{upscaled_filename}"
    
                # 4. Set final art source URL
                if hosted_upscaled_art_url:
                    final_art_source_url = hosted_upscaled_art_url
                    if self.upscaler_outscale_factor > 0:
                        art_zoom /= self.upscaler_outscale_factor
                        logger.info(f"Adjusted artZoom for upscaled image to: {art_zoom:.4f}")
                elif hosted_original_art_url:
                    final_art_source_url = hosted_original_art_url
    
            # --- Set Symbol and P/T ---
            set_symbol_x = self.frame_config.get("set_symbol_x", 0.0); set_symbol_y = self.frame_config.get("set_symbol_y", 0.0); set_symbol_zoom = self.frame_config.get("set_symbol_zoom", 0.1)
            actual_set_code_for_url = self.set_symbol_override.lower() if self.set_symbol_override else set_code_from_scryfall.lower()
            set_symbol_source_url = f"{ccProto}://{ccHost}:{ccPort}/img/setSymbols/official/{actual_set_code_for_url}-{rarity_code_for_symbol}.svg"
            if self.auto_fit_set_symbol and set_symbol_source_url:
                auto_fit_symbol_params_result = self._calculate_auto_fit_set_symbol_params(set_symbol_source_url)
                if auto_fit_symbol_params_result and auto_fit_symbol_params_result.get("_status", "").startswith("success"):
                    set_symbol_x, set_symbol_y, set_symbol_zoom = auto_fit_symbol_params_result["setSymbolX"], auto_fit_symbol_params_result["setSymbolY"], auto_fit_symbol_params_result["setSymbolZoom"]
    
            power_val = card_data.get('power', ''); toughness_val = card_data.get('toughness', ''); pt_text_final = ""
            if 'power' in card_data and 'toughness' in card_data:
                if self.frame_type == "8th":
                    power_val = "X" if power_val == "*" else power_val
                    toughness_val = "X" if toughness_val == "*" else toughness_val
                pt_text_final = f"{power_val}/{toughness_val}"
            
            display_title_text = basic_land_type_override if is_basic_land_fetch_mode and basic_land_type_override else scryfall_card_name
            
            rules_text_config = self.frame_config.get("text", {}).get("rules", {})
            type_text_config = self.frame_config.get("text", {}).get("type", {})
    
            rules_font_size = calculate_font_size(final_rules_text, rules_text_config.get("width", 0.8), rules_text_config.get("height", 0.28), rules_text_config.get("size", 0.036))
            type_font_size = calculate_font_size(card_data.get('type_line', ''), type_text_config.get("width", 0.8), type_text_config.get("height", 0.05), type_text_config.get("size", 0.032))
    
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
                    "type": {**self.frame_config.get("text", {}).get("type", {}), "text": card_data.get('type_line', 'Instant'), "size": type_font_size},
                    "rules": { **self.frame_config.get("text", {}).get("rules", {}), "text": final_rules_text, "size": rules_font_size },
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
