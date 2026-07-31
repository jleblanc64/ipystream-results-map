import os
import ipystream
from python.utils_login import get_username_password

# fill your parameters (you may only fill the password and keep the rest)
os.environ["username"] = "ronquoz21@sympheny.com"
os.environ["password"] = get_username_password("/home/charles/Downloads/creds.properties")[1]
os.environ["project_id"] = "esp-80861e90-7276-451e-930f-3f29"

# runs function run() in python/notebook.py
ipystream.run()