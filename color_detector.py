# --- file: color_detector.py ---
"""
Module for detecting card colors from Scryfall data
"""
import logging
from typing import Dict, List, Union

from color_mapping import COLOR_CODE_MAP

logger = logging.getLogger(__name__)

class ColorDetector:
    """Class for detecting card colors from Scryfall data"""
    
    @staticmethod
    def detect_dual_land_colors(card_data: Dict) -> List[Dict]:
        """
        Detect dual land colors by analyzing oracle text.
        Returns a list of color dictionaries in the order they appear in the text.
        """
        # First verify this is a land card
        if 'type_line' not in card_data or 'Land' not in card_data['type_line']:
            return []
            
        # Need oracle text to detect mana symbols
        if 'oracle_text' not in card_data or not card_data['oracle_text']:
            return []
            
        oracle_text = card_data['oracle_text']
        # logger.info(f"Analyzing oracle text for dual land: {oracle_text}") # Optional: can be verbose
        
        mana_positions = []
        for color_key, color_info in COLOR_CODE_MAP.items():
            if color_key in ['L', 'C', 'M', 'A']:  # Skip Land, Colorless, Multicolor, Artifact base types
                continue
                
            mana_symbol = f"{{{color_key}}}" # e.g. {W}, {U}
            # Simple find, might need regex for more complex cases like Phyrexian mana if you support it
            index = oracle_text.find(mana_symbol) 
            
            if index != -1: # Ensure find was successful
                mana_positions.append({
                    "position": index,
                    "color_key": color_key, # Store the key e.g. 'W'
                    "color_info": color_info # Store the dict e.g. {'code': 'w', ...}
                })
                # logger.info(f"Found {color_key} at position {index}") # Optional

        if len(mana_positions) >= 2:
            mana_positions.sort(key=lambda x: x["position"])
            # logger.info(f"Sorted mana positions for dual land: {[(item['color_key'], item['position']) for item in mana_positions]}")
            
            # Return Land as the base color and then the mana colors in order
            # Ensure 'L' is defined in COLOR_CODE_MAP
            return [COLOR_CODE_MAP.get('L', {'code': 'l', 'name': 'Land'})] + [item["color_info"] for item in mana_positions]
        
        return []

    @staticmethod
    def get_color_info(card_data: Dict) -> Union[Dict, List[Dict]]:
        """Extract color information from card data.
        
        For dual lands and multicolor cards, may return a list of color info dictionaries or a complex dict.
        """
        card_name = card_data.get('name', 'Unknown Card') # For logging

        # --- Artifact Check ---
        if 'type_line' in card_data and 'Artifact' in card_data['type_line']:
            logger.debug(f"Card '{card_name}' is an Artifact.")
            # Check if it's a colored artifact based on Scryfall 'colors' field
            if 'colors' in card_data and card_data['colors']:
                scryfall_artifact_colors = card_data['colors']
                num_artifact_colors = len(scryfall_artifact_colors)

                if num_artifact_colors >= 2: # Multicolor Artifact
                    component_colors = [COLOR_CODE_MAP[c] for c in scryfall_artifact_colors if c in COLOR_CODE_MAP]
                    if component_colors:
                        logger.debug(f"'{card_name}' is a Multicolor Artifact. Colors: {scryfall_artifact_colors}")
                        # Use 'MA' (Multicolor Artifact) if defined, else 'M' or 'A'
                        base_code = COLOR_CODE_MAP.get('MA', COLOR_CODE_MAP.get('M', COLOR_CODE_MAP.get('A', COLOR_CODE_MAP['C'])))
                        return {
                            **base_code,
                            'name': "Multicolored Artifact", # Or construct from component names
                            'is_gold': True, # Treat as gold for frame purposes
                            'is_artifact': True,
                            'component_colors': component_colors
                        }
                elif num_artifact_colors == 1: # Monocolored Artifact
                    color_key = scryfall_artifact_colors[0]
                    if color_key in COLOR_CODE_MAP:
                        logger.debug(f"'{card_name}' is a {COLOR_CODE_MAP[color_key]['name']} Artifact.")
                        return {
                            **COLOR_CODE_MAP[color_key], # Base is its color
                            'name': f"{COLOR_CODE_MAP[color_key]['name']} Artifact",
                            'is_artifact': True,
                            # 'original_code': COLOR_CODE_MAP[color_key]['code'] # Keep track of base color if needed
                        }
            
            # If not a colored artifact (or colors field is empty), it's a "plain" colorless artifact.
            # Prefer 'A' mapping if it exists (for M15 frames: m15PTA.png, a.png)
            logger.debug(f"'{card_name}' is a Colorless Artifact.")
            return COLOR_CODE_MAP.get('A', COLOR_CODE_MAP.get('C', {})) # Fallback to 'C', then empty dict


        # --- Land Check ---
        if 'type_line' in card_data and 'Land' in card_data['type_line']:
            logger.debug(f"Card '{card_name}' is a Land.")
            # Try to detect dual/multi-color producing lands from oracle text
            multi_land_colors = ColorDetector.detect_dual_land_colors(card_data) # Renamed for clarity
            if multi_land_colors: # Found 2 or more mana symbols
                logger.debug(f"Detected multicolor land colors for '{card_name}': {[c['name'] for c in multi_land_colors[1:]]}")
                return multi_land_colors
            
            # Check for basic lands by name in type_line (more robust than checking 'Basic Land')
            # Scryfall 'colors' field is usually empty for basic lands, but identity might be set.
            # We primarily rely on type line for basic lands.
            type_line_lower = card_data['type_line'].lower()
            basic_land_map = {
                'plains': 'W', 'island': 'U', 'swamp': 'B', 'mountain': 'R', 'forest': 'G'
            }
            for land_name_part, color_key in basic_land_map.items():
                if land_name_part in type_line_lower:
                    logger.debug(f"Detected basic land '{card_name}' as {COLOR_CODE_MAP[color_key]['name']}.")
                    return [COLOR_CODE_MAP.get('L', {'code':'l', 'name':'Land'}), COLOR_CODE_MAP[color_key]]
            
            # If not a multi-color land or known basic, it's a generic land (e.g., Wastes, or non-basic without clear mana symbols)
            logger.debug(f"'{card_name}' is a generic/other Land.")
            return COLOR_CODE_MAP.get('L', {'code':'l', 'name':'Land'})
                

        # --- Other Card Types (Creatures, Enchantments, Sorceries, Instants) ---
        if 'colors' in card_data and card_data['colors'] is not None: # Ensure 'colors' exists and is not None
            scryfall_colors = card_data['colors'] # e.g. ['R', 'G']
            num_colors = len(scryfall_colors)

            if num_colors == 0: # Colorless non-artifact, non-land (e.g. some Eldrazi)
                logger.debug(f"Card '{card_name}' is Colorless (non-artifact, non-land).")
                return COLOR_CODE_MAP.get('C', {})
            
            elif num_colors == 1: # Monocolored
                color_key = scryfall_colors[0]
                if color_key in COLOR_CODE_MAP:
                    logger.debug(f"Card '{card_name}' is Monocolored: {COLOR_CODE_MAP[color_key]['name']}.")
                    return COLOR_CODE_MAP[color_key]
                else:
                    logger.warning(f"Unknown monocolor key '{color_key}' for card '{card_name}'. Defaulting.")
                    return COLOR_CODE_MAP.get('C', {}) # Fallback
            
            elif num_colors >= 2: # Multicolor / Gold
                logger.debug(f"Card '{card_name}' is Multicolor/Gold. Colors: {scryfall_colors}.")
                component_color_dicts = []
                for color_key in scryfall_colors: # Scryfall 'colors' are usually in WUBRG order
                    if color_key in COLOR_CODE_MAP:
                        component_color_dicts.append(COLOR_CODE_MAP[color_key])
                
                if component_color_dicts:
                    # Use 'M' (multicolor) as the base, and provide the components.
                    # CardBuilder will use 'component_colors' to pick primary/secondary.
                    base_multicolor_info = COLOR_CODE_MAP.get('M', {'code': 'm', 'name': 'Multicolored'})
                    return {
                        **base_multicolor_info,
                        'is_gold': True,
                        'component_colors': component_color_dicts # List of color dicts, ordered by WUBRG
                    }
                else:
                    logger.warning(f"Could not map component colors for gold card '{card_name}'. Colors: {scryfall_colors}. Defaulting.")
                    return COLOR_CODE_MAP.get('M', COLOR_CODE_MAP.get('C', {})) # Fallback
        
        logger.warning(f"No color explicitly found for '{card_name}' (not artifact, not land, no 'colors' field). Defaulting to Colorless.")
        return COLOR_CODE_MAP.get('C', {}) # Final fallback