# --- scryfall_processor.py ---
"""
Main processor for converting Scryfall card data to CardConjurer format
"""
import sys
import json
import time
import logging
import re 
from typing import Dict, List, Optional

from scryfall_api_utils import ScryfallAPI 
from color_detector import ColorDetector
from card_builder import CardBuilder
from frame_configs import get_frame_config

logger = logging.getLogger(__name__)

class ScryfallCardProcessor:
    """Main class for processing cards from Scryfall to CardConjurer format"""
    
    def __init__(self, 
                 input_file: Optional[str], 
                 frame_type: str = "seventh", 
                 frame_set: str = "regular", 
                 legendary_crowns: bool = False, 
                 auto_fit_art: bool = False, 
                 set_symbol_override: Optional[str] = None, 
                 auto_fit_set_symbol: bool = False, 
                 api_delay_seconds: float = 0.1,
                 fetch_basic_land_type: Optional[str] = None,
                 
                 # Upscaling parameters
                 upscale_art: bool = False,
                 ilaria_upscaler_base_url: Optional[str] = None,
                 upscaler_model_name: str = "RealESRGAN_x2plus",
                 upscaler_outscale_factor: int = 2,
                 upscaler_denoise_strength: float = 0.5,
                 upscaler_face_enhance: bool = False,
                 
                 # Nginx WebDAV Image Hosting params
                 image_server_base_url: Optional[str] = None,
                 image_server_path_prefix: str = "/webdav_images"
                ): 
        
        self.input_file = input_file
        self.frame_type = frame_type
        self.frame_set = frame_set
        self.legendary_crowns = legendary_crowns
        self.auto_fit_art = auto_fit_art
        self.set_symbol_override = set_symbol_override
        self.auto_fit_set_symbol = auto_fit_set_symbol
        self.api_delay_seconds = api_delay_seconds
        self.fetch_basic_land_type = fetch_basic_land_type 
        
        self.upscale_art = upscale_art
        self.ilaria_upscaler_base_url = ilaria_upscaler_base_url
        self.upscaler_model_name = upscaler_model_name
        self.upscaler_outscale_factor = upscaler_outscale_factor
        self.upscaler_denoise_strength = upscaler_denoise_strength
        self.upscaler_face_enhance = upscaler_face_enhance
        
        self.image_server_base_url = image_server_base_url
        self.image_server_path_prefix = image_server_path_prefix

        logger.debug(f"ScryfallCardProcessor __init__: upscale_art='{self.upscale_art}', image_server_base_url='{self.image_server_base_url}'")

        self.frame_config = get_frame_config(frame_type)
        self.scryfall_api = ScryfallAPI()  

        self.card_builder = CardBuilder(
            frame_type=self.frame_type, 
            frame_config=self.frame_config, 
            frame_set=self.frame_set, 
            legendary_crowns=self.legendary_crowns, 
            auto_fit_art=self.auto_fit_art, 
            set_symbol_override=self.set_symbol_override, 
            auto_fit_set_symbol=self.auto_fit_set_symbol, 
            api_delay_seconds=self.api_delay_seconds,
            
            upscale_art=self.upscale_art,
            ilaria_upscaler_base_url=self.ilaria_upscaler_base_url,
            upscaler_model_name=self.upscaler_model_name,
            upscaler_outscale_factor=self.upscaler_outscale_factor,
            upscaler_denoise_strength=self.upscaler_denoise_strength,
            upscaler_face_enhance=self.upscaler_face_enhance,
            
            image_server_base_url=self.image_server_base_url,
            image_server_path_prefix=self.image_server_path_prefix
        ) 
    
    def load_cards_from_file(self) -> List[str]:
        card_names = []
        if not self.input_file: return []
        try:
            with open(self.input_file, 'r', encoding='utf-8') as file:
                for line in file:
                    processed_line = line.strip()
                    match = re.match(r"^\d+\s*[xX]?\s*(.+)", processed_line)
                    if match: card_name = match.group(1).strip()
                    elif processed_line and not processed_line.startswith('#') and not processed_line.isspace(): card_name = processed_line
                    else: continue
                    if card_name: card_names.append(card_name)
            logger.info(f"Loaded {len(card_names)} patterns from: {self.input_file}")
            return card_names
        except Exception as e: logger.error(f"Error reading {self.input_file}: {e}"); return []
    
    def process_cards(self) -> List[Dict]:
        items_to_process = [] 
        if self.fetch_basic_land_type:
            logger.info(f"Mode: Fetching basic land: {self.fetch_basic_land_type}")
            for printing_data in self.scryfall_api.get_all_printings_of_basic_land(self.fetch_basic_land_type):
                name = printing_data.get("name", self.fetch_basic_land_type)
                key = f"{name}-{printing_data.get('set', 'UNK')}-{printing_data.get('collector_number', '0')}"
                items_to_process.append({"key_name": key, "card_data_obj": printing_data, "is_basic_land_fetch_item": True})
        elif self.input_file: 
            logger.info(f"Mode: Processing from file: {self.input_file}")
            for name in self.load_cards_from_file():
                items_to_process.append({"key_name": name, "name_to_fetch": name, "is_basic_land_fetch_item": False})
        else: logger.error("No input source."); return []

        if not items_to_process: logger.warning("No items to process."); return []
            
        result = []
        for i, item in enumerate(items_to_process):
            card_key = item["key_name"]
            is_basic = item["is_basic_land_fetch_item"]
            scryfall_data = item["card_data_obj"] if is_basic else self.scryfall_api.get_earliest_printing(item["name_to_fetch"])
            
            log_prefix = f"Basic land ({i+1}/{len(items_to_process)})" if is_basic else f"Card from file ({i+1}/{len(items_to_process)})"
            logger.info(f"{log_prefix}: {card_key}" + (f" (Set: {scryfall_data.get('set')})" if scryfall_data else ""))

            if not scryfall_data: logger.warning(f"No Scryfall data for '{card_key}', skipping."); continue
            
            try:
                color_info = ColorDetector.get_color_info(scryfall_data) 
                card_object = self.card_builder.build_card_data(
                    card_name=card_key, card_data=scryfall_data, color_info=color_info,
                    is_basic_land_fetch_mode=is_basic,
                    basic_land_type_override=self.fetch_basic_land_type if is_basic else None
                )
                result.append(card_object)
            except Exception as e: logger.error(f"Error processing '{card_key}': {e}", exc_info=True)
            
            if self.api_delay_seconds > 0 and i < len(items_to_process) - 1: time.sleep(self.api_delay_seconds)
        return result
    
    def save_output(self, output_file: str, data: List[Dict]):
        try:
            with open(output_file, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2)
            logger.info(f"Output saved to {output_file}")
        except Exception as e: logger.error(f"Error saving output: {e}")
