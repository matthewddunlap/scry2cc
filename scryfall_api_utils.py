"""
Utility functions for interacting with the Scryfall API
"""
import logging
import requests
from typing import Dict, Optional

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

    def search_cards(self, query: str, unique="prints", order_by="released", direction="asc") -> Optional[Dict]:
        """Search for cards using the Scryfall API."""
        try:
            response = requests.get(
                f"{self.base_url}/cards/search",
                params={
                    "q": query,
                    "unique": unique,
                    "order": order_by,
                    "dir": direction
                },
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to search cards with query '{query}': {response.status_code} - {response.text}")
                return None
        except requests.RequestException as e:
            logger.error(f"Error searching cards with query '{query}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error searching cards with query '{query}': {e}")
            return None

    def get_earliest_printing(self, card_name: str) -> Optional[Dict]:
        """Get the earliest printing of a card based on set release dates."""
        try:
            # First get the Oracle ID from the main card
            card_data = self.get_card_by_name(card_name)
            if not card_data or 'oracle_id' not in card_data:
                logger.error(f"Failed to get oracle ID for card '{card_name}'")
                return None
            
            oracle_id = card_data['oracle_id']
            
            # Search for all printings of the card
            search_result = self.search_cards(f"oracle_id:{oracle_id}", "prints", "released", "asc")
            
            if search_result and 'data' in search_result and search_result['data']:
                # The first card should be the earliest printing due to our sorting parameters
                earliest_card = search_result['data'][0]
                
                # Double check with the set data to be sure
                set_code = earliest_card.get('set')
                if set_code:
                    set_data = self.get_set_data(set_code)
                    if set_data and 'released_at' in set_data:
                        logger.info(f"Confirmed earliest printing of '{card_name}': {set_code} ({set_data['released_at']})")
                
                return earliest_card
            else:
                logger.error(f"No printings found for card '{card_name}'")
                return None
        except Exception as e:
            logger.error(f"Unexpected error getting earliest printing for '{card_name}': {e}")
            return None
