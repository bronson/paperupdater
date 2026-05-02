#!/usr/bin/python3
import logging
import os
import re
import time

import requests

# paper specific configuration
PAPER_API_BASE = "https://api.papermc.io/v2"
LOG_FILENAME = "paper_updater.log"
LOG_FORMAT = "%(asctime)s %(message)s"

# Log to both the log file and the screen
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter(LOG_FORMAT)

file_handler = logging.FileHandler(LOG_FILENAME)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def download_server(version, build):
    logging.info(f"Downloading Paper version {version}-{build}.")
    jar_data = requests.get(PAPER_API_BASE + "/projects/paper/versions/" + version
        + "/builds/" + str(build) + "/downloads/paper-" + version + "-" + str(build) + ".jar")
    with open(f"paper-{version}-{build}.jar", "wb") as jar_file:
        jar_file.write(jar_data.content)
    jar_file.close()
    logging.info(f"Wrote jar to paper-{version}-{build}.jar")

def remove_old_servers(filelist):
    logging.info(f"Cleaning up {len(filelist)} old server(s)")
    for file in filelist:
        logging.info(f"Deleting {file}")
        os.remove(file)
    logging.info("Cleanup complete.")

def main():
    logging.info("----------------")
    # retrieve version manifest
    logging.info("Getting Paper release versions")
    response = requests.get(PAPER_API_BASE + "/projects/paper")
    if not response.ok:
        print(f"Error fetching Paper versions: {response.status_code} {response.reason}")
        return
    data = response.json()
    latest_paper_ver = data["versions"][-1]
    response = requests.get(PAPER_API_BASE + "/projects/paper/versions/" + latest_paper_ver)
    if not response.ok:
        print(f"Error fetching builds for {latest_paper_ver}: {response.status_code} {response.reason}")
        return
    latest_paper_build = response.json()["builds"][-1]

    logging.info(f"The latest version of Paper is {latest_paper_ver}, build {latest_paper_build}.")

    jar_filename = f"paper-{latest_paper_ver}-{latest_paper_build}.jar"
    if os.path.isfile(jar_filename):
        logging.info(f"already have {jar_filename}")
    else:
        download_server(latest_paper_ver, latest_paper_build)

if __name__ == "__main__":
    main()
