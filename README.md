# scry2cc
Generate Card Conjurer import file from Scryfall

## Summary
[scry2cc](https://github.com/matthewddunlap/scry2cc) takes a deck list as input and queries [Scryfall](https://scryfall.com/) via API to fetch card data then formats it in JSON as a `.cardconjurer` file for automated processing via [ccDownloader](https://github.com/matthewddunlap/ccDownloader).

Card art can optionally be saved locally to a webserver (that allows PUT requests) via the `--image_server_base_url` and `--image_server_path_prefix` parameters. This will minimize the data that needs to be pulled from Scryfall.

Cart art can optionally be upscaled using [Ilaria Upscaler](https://huggingface.co/spaces/TheStinger/Ilaria_Upscaler) via the `--upscale_art` parameter. The resulting upscaled are is saved locally for reuse without regeneration.

## Requirements
`gradio_client` is requried to use the `--upscale_art` feature.

Use a python virtual environment to install the requirements
```
apt install python3.11-venv
cd scry2cc
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

If not using the `--upscale_art` feature the core requirments can be installed via `apt` on Debian 12.
```
apt install python3-lxml python3-natsort python3-pil python3-requests
```

## Examples
### Create Card Conjurer save file for import from saved deck list
This is the most common use case of preparing a deck list for download with [ccDownloader](https://github.com/matthewddunlap/ccDownloader) followed by printing with [MtgPng2Pdf](https://github.com/matthewddunlap/MtgPng2Pdf).
```
python3 scry2cc.py --frame seventh --auto_fit_art --auto_fit_set_symbol --output_file myDeck.cardconjurer myDeck.txt
```

Same as above but save the original art to local webserver.
```
python3 scry2cc.py --image_server_base_url http://mywebserver:4242 --image_server_path_prefix local_art --frame seventh --auto_fit_art --auto_fit_set_symbol --output_file myDeck.cardconjurer myDeck.txt
```


### Create Card Conjurer save file for import of all unique art prints of forest
This is an optional one time operation. By default [MtgPng2Pdf](https://github.com/matthewddunlap/MtgPng2Pdf) will use a random selection of land art.
```
python3 scry2cc.py --frame seventh --auto_fit_art --auto_fit_set_symbol --fetch_basic_land Forest --output_file forest.cardconjurer
```

Same as above but upscale the art and save both the original and the upscaled art to local webserver.
```
python3 scry2cc.py --ilaria_base_url https://thestinger-ilaria-upscaler.hf.space --upscaler_model_name RealESRGAN_x2plus --upscaler_outscale_factor 4 --frame seventh --auto_fit_art --auto_fit_set_symbol --fetch_basic_land Forest --output_file forest.cardconjurer
```
