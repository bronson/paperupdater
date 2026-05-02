#!/usr/bin/python3
import re
import os
import time
import logging
import requests


# paper specific configuration
PAPER_API_BASE = "https://papermc.io/api/v1"
LOG_FILENAME = 'paper_updater.log'
LOG_FORMAT = '%(asctime)s %(message)s'

logging.basicConfig(filename=LOG_FILENAME,level=logging.INFO, format=LOG_FORMAT)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
def download_server(version, build):
    logging.info(f"Downloading Paper version {version}, build number {build}.")
    jar_data = requests.get(PAPER_API_BASE + "/paper/" + version + "/latest/download")
    with open(f"paper-{version}-{build}.jar", "wb") as jar_file:
        jar_file.write(jar_data.content)
    jar_file.close()
    logging.info(f"Written updated jar file to paper-{version}-{build}.jar.")
    logging.info("Update complete. Please make any necessary changes to any start scripts, and restart the server.")

def remove_old_servers(filelist):
    logging.info(f"Cleaning up {len(filelist)} old server(s)...")
    for file in filelist:
        logging.info(f"Deleting {file}...")
        os.remove(file)
    logging.info("Cleanup complete.")

def main():
    logging.info("Starting update run...")
    # retrieve version manifest
    logging.info("Getting Paper release versions...")
    response = requests.get(PAPER_API_BASE + "/paper")
    data = response.json()
    latest_paper_ver = data["versions"][0]
    response = requests.get(PAPER_API_BASE + "/paper/" + latest_paper_ver)
    latest_paper_build = int(response.json()["builds"]["latest"])

    logging.info(f'The latest version of Paper is {latest_paper_ver}, build {latest_paper_build}.')

    jar_filename = f"paper-{latest_paper_ver}-{latest_paper_build}.jar"
    if os.path.isfile(jar_filename):
        print(f"You already have {filename}")
    else:
        download_server(latest_paper_ver, latest_paper_build)
        exit(0)

if __name__ == "__main__":
    main()
    logging.info("\n")