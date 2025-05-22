"""
Utility functions for interacting with the Scryfall API
"""
import logging
import requests
import time
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

class ScryfallAPI:
    def __init__(self):
        self.base_url = "https://api.scryfall.com"
    
    def get_card_by_name(self, card_name: str) -> Optional[Dict]:
        """Fetch card data from Scryfall API by name."""
        try:
            # URL encode the card name for the API request
            response = requests.get(
                f"{self.base_url}/cards/named",
                params={"fuzzy": card_name},
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to find card '{card_name}': {response.status_code} - {response.text}")
                return None
        except requests.RequestException as e:
            logger.error(f"Error fetching card '{card_name}' from Scryfall: {e}")
            return None

    def get_set_data(self, set_code: str) -> Optional[Dict]:
        """Get detailed information about a set from Scryfall."""
        try:
            response = requests.get(
                f"{self.base_url}/sets/{set_code}",
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get set data for '{set_code}': {response.status_code} - {response.text}")
                return None
        except requests.RequestException as e:
            logger.error(f"Error fetching set data for '{set_code}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting set data for '{set_code}': {e}")
            return None

    # --- MODIFIED search_cards to handle pagination and return List[Dict] ---
    def search_cards(self, query: str, unique="prints", order_by="released", direction="asc") -> List[Dict]:
        """Search for cards using the Scryfall API. Returns a list of all cards matching the query by handling pagination."""
        all_cards = []
        search_url = f"{self.base_url}/cards/search"
        params = {
            "q": query,
            "unique": unique,
            "order": order_by,
            "dir": direction
        }
        
        page_num = 1
        current_search_url = search_url # Use a variable for the current page URL

        while current_search_url:
            try:
                # Only pass params on the first request, subsequent requests use the full next_page URL
                current_params = params if page_num == 1 else None
                # logger.debug(f"Fetching page {page_num} for query '{query}': {current_search_url} with params {current_params}")
                
                response = requests.get(current_search_url, params=current_params, timeout=20)
                response.raise_for_status() 
                
                page_data = response.json()
                data_list = page_data.get('data', [])
                if not data_list and page_num == 1: # No data on first page
                    logger.info(f"No cards found for query: {query}")
                    return []
                
                all_cards.extend(data_list)
                
                current_search_url = page_data.get('next_page') 
                page_num += 1
                if current_search_url:
                    # logger.debug(f"Found next page: {current_search_url}")
                    time.sleep(0.1) # Scryfall API polite delay
                # else:
                    # logger.debug("No more pages found.")

            except requests.exceptions.HTTPError as http_err:
                logger.error(f"HTTP error occurred while searching cards (query: '{query}', page: {page_num}): {http_err} - {response.text if response else 'No response text'}")
                break 
            except requests.RequestException as req_err:
                logger.error(f"Request error occurred while searching cards (query: '{query}', page: {page_num}): {req_err}")
                break
            except Exception as e:
                logger.error(f"Unexpected error searching cards (query: '{query}', page: {page_num}): {e}")
                break
        
        if page_num > 2 or (page_num == 2 and not current_search_url): # Log only if multiple pages or only one full page
             logger.info(f"Found {len(all_cards)} total cards across {page_num-1} page(s) for query: {query}")
        return all_cards

    def get_earliest_printing(self, card_name: str) -> Optional[Dict]:
        # ... (original implementation, but now search_cards returns a List)
        try:
            card_data = self.get_card_by_name(card_name)
            if not card_data or 'oracle_id' not in card_data:
                return None # Error logged in get_card_by_name
            
            oracle_id = card_data['oracle_id']
            
            # search_cards now returns List[Dict]
            search_results_list = self.search_cards(f"oracle_id:{oracle_id}", "prints", "released", "asc")
            
            if search_results_list: # Check if the list is not empty
                earliest_card = search_results_list[0]
                set_code = earliest_card.get('set')
                if set_code:
                    set_data = self.get_set_data(set_code)
                    if set_data and 'released_at' in set_data:
                        logger.info(f"Confirmed earliest printing of '{card_name}': {set_code} ({set_data['released_at']})")
                return earliest_card
            else:
                logger.error(f"No printings found for card '{card_name}' with oracle_id {oracle_id}") # Adjusted log
                return None
        except Exception as e:
            logger.error(f"Unexpected error getting earliest printing for '{card_name}': {e}")
            return None

    # --- NEW METHOD for Basic Lands ---
    def get_all_printings_of_basic_land(self, land_name: str) -> List[Dict]:
        """
        Fetches all non-full-art printings of a specific basic land type, unique by art.
        Example land_name: "Forest", "Island", etc.
        """
        query = f'!"{land_name}" type:basic is:notfullart'
        unique_strategy = "art"
        order_strategy = "released" 
        direction_strategy = "asc"  

        logger.info(f"Fetching all non-full-art printings (unique by art) for basic land: {land_name} (Query: {query})")
        
        all_printings = self.search_cards(
            query=query, 
            unique=unique_strategy, 
            order_by=order_strategy, 
            direction=direction_strategy
        )
        
        if not all_printings:
            logger.warning(f"No non-full-art printings found for basic land '{land_name}'.")
        
        return all_printings
