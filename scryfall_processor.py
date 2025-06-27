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
                 art_mode: str = "earliest",
                 
                 # Upscaling parameters
                 upscale_art: bool = False,
                 ilaria_upscaler_base_url: Optional[str] = None,
                 upscaler_model_name: str = "RealESRGAN_x2plus",
                 upscaler_outscale_factor: int = 2,
                 upscaler_denoise_strength: float = 0.5,
                 upscaler_face_enhance: bool = False,
                 
                 # Output parameters
                 image_server_base_url: Optional[str] = None,
                 image_server_path_prefix: str = "/webdav_images",
                 
                 # NEW: Local output directory
                 output_dir: Optional[str] = None
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
        self.art_mode = art_mode
        
        self.upscale_art = upscale_art
        self.ilaria_upscaler_base_url = ilaria_upscaler_base_url
        self.upscaler_model_name = upscaler_model_name
        self.upscaler_outscale_factor = upscaler_outscale_factor
        self.upscaler_denoise_strength = upscaler_denoise_strength
        self.upscaler_face_enhance = upscaler_face_enhance
        
        self.image_server_base_url = image_server_base_url
        self.image_server_path_prefix = image_server_path_prefix
        
        # NEW: Store output directory
        self.output_dir = output_dir

        logger.debug(f"ScryfallCardProcessor __init__: upscale_art='{self.upscale_art}', image_server_base_url='{self.image_server_base_url}', art_mode='{self.art_mode}', output_dir='{self.output_dir}'")

        self.frame_config = get_frame_config(frame_type)
        self.scryfall_api = ScryfallAPI()  

        # MODIFIED: Pass output_dir to CardBuilder
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
            image_server_path_prefix=self.image_server_path_prefix,
            
            # NEW: Pass the output directory
            output_dir=self.output_dir
        ) 
    
    def format_card_filename(self, card_data: Dict) -> str:
        """
        Format card data into filename: [card-name]_[set]_[number]
        - card name has spaces replaced with dashes
        - special characters removed/replaced
        - all lowercase
        - delimited with underscores
        NOTE: No .png extension added here as it's handled downstream
        """
        import re
        
        card_name = card_data.get('name', 'unknown')
        set_code = card_data.get('set', 'unk')
        collector_number = card_data.get('collector_number', '0')
        
        # Clean card name: lowercase, replace spaces with dashes, remove special chars
        clean_name = re.sub(r'[^\w\s-]', '', card_name.lower())  # Remove special chars except spaces and dashes
        clean_name = re.sub(r'\s+', '-', clean_name.strip())     # Replace spaces with dashes
        clean_name = re.sub(r'-+', '-', clean_name)              # Collapse multiple dashes
        
        # Clean set code and collector number
        clean_set = set_code.lower()
        clean_number = collector_number
        
        return f"{clean_name}_{clean_set}_{clean_number}"
    
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
    
    def get_card_data_by_art_mode(self, card_name: str) -> List[Dict]:
        """
        Get card data based on the art mode.
        Returns a list of card data objects (single item for earliest/latest, multiple for all_art).
        """
        if self.art_mode == "earliest":
            card_data = self.scryfall_api.get_earliest_printing(card_name)
            return [card_data] if card_data else []
        elif self.art_mode == "latest":
            card_data = self.scryfall_api.get_latest_printing(card_name)
            return [card_data] if card_data else []
        elif self.art_mode == "all_art":
            return self.scryfall_api.get_all_art_printings(card_name)
        else:
            logger.error(f"Unknown art mode: {self.art_mode}")
            return []
    
    def process_cards(self) -> List[Dict]:
        items_to_process = [] 
        if self.fetch_basic_land_type:
            logger.info(f"Mode: Fetching basic land: {self.fetch_basic_land_type}")
            for printing_data in self.scryfall_api.get_all_printings_of_basic_land(self.fetch_basic_land_type):
                name = printing_data.get("name", self.fetch_basic_land_type)
                key = f"{name}-{printing_data.get('set', 'UNK')}-{printing_data.get('collector_number', '0')}"
                items_to_process.append({"key_name": key, "card_data_obj": printing_data, "is_basic_land_fetch_item": True})
        elif self.input_file: 
            logger.info(f"Mode: Processing from file: {self.input_file} (art mode: {self.art_mode})")
            for name in self.load_cards_from_file():
                items_to_process.append({"key_name": name, "name_to_fetch": name, "is_basic_land_fetch_item": False})
        else: logger.error("No input source."); return []

        if not items_to_process: logger.warning("No items to process."); return []
            
        result = []
        for i, item in enumerate(items_to_process):
            card_key = item["key_name"]
            is_basic = item["is_basic_land_fetch_item"]
            
            if is_basic:
                # Basic land fetch mode - use existing logic
                scryfall_data_list = [item["card_data_obj"]]
            else:
                # File mode - use art mode
                scryfall_data_list = self.get_card_data_by_art_mode(item["name_to_fetch"])
            
            if not scryfall_data_list:
                logger.warning(f"No Scryfall data for '{card_key}', skipping.")
                continue
            
            # Process each card data object (multiple for all_art mode)
            for j, scryfall_data in enumerate(scryfall_data_list):
                # Create unique key for each printing when using all_art mode
                if len(scryfall_data_list) > 1:
                    # Format: [card-name]_[set]_[number].png
                    printing_key = self.format_card_filename(scryfall_data)
                    log_prefix = f"Card from file ({i+1}/{len(items_to_process)}, art {j+1}/{len(scryfall_data_list)})"
                else:
                    # For single printings, still use the new format for consistency
                    if is_basic:
                        printing_key = card_key  # Keep original format for basic lands
                    else:
                        printing_key = self.format_card_filename(scryfall_data)
                    log_prefix = f"Basic land ({i+1}/{len(items_to_process)})" if is_basic else f"Card from file ({i+1}/{len(items_to_process)})"
                
                logger.info(f"{log_prefix}: {printing_key}" + (f" (Set: {scryfall_data.get('set')})" if scryfall_data else ""))

                try:
                    color_info = ColorDetector.get_color_info(scryfall_data) 
                    card_object = self.card_builder.build_card_data(
                        card_name=printing_key, 
                        card_data=scryfall_data, 
                        color_info=color_info,
                        is_basic_land_fetch_mode=is_basic,
                        basic_land_type_override=self.fetch_basic_land_type if is_basic else None
                    )
                    result.append(card_object)
                except Exception as e: 
                    logger.error(f"Error processing '{printing_key}': {e}", exc_info=True)
                
                # Add delay between individual printings when processing multiple arts
                if self.api_delay_seconds > 0 and j < len(scryfall_data_list) - 1:
                    time.sleep(self.api_delay_seconds)
            
            # Add delay between different cards
            if self.api_delay_seconds > 0 and i < len(items_to_process) - 1:
                time.sleep(self.api_delay_seconds)
        return result
    
    def save_output(self, output_file: str, data: List[Dict]):
        try:
            with open(output_file, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2)
            logger.info(f"Output saved to {output_file}")
        except Exception as e: logger.error(f"Error saving output: {e}")
