

from __future__ import print_function

import os.path
import re
import urllib.parse as urlparse
from urllib.parse import parse_qs

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

G_DRIVE_DIR_MIME_TYPE = "application/vnd.google-apps.folder"
G_DRIVE_FILE_LINK = "https://drive.google.com/open?id={}"
G_DRIVE_FOLDER_LINK = "https://drive.google.com/drive/folders/{}"


def _get_file_id(link: str):
    if "folders" in link or "file" in link:
        regex = r"https://drive\.google\.com/(drive)?/?u?/?\d?/?(mobile)?/?(file)?(folders)?/?d?/([-\w]+)[?+]?/?(w+)?"
        res = re.search(regex, link)
        if res is None:
            return res
        return res.group(5)
    parsed = urlparse.urlparse(link)
    return parse_qs(parsed.query)['id'][0]


class DriveAPI:
    global SCOPES

    # Define the scopes
    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(self):

        # Variable self.creds will
        # store the user access token.
        # If no valid token found
        # we will create one.
        self.creds = None
        token_file = "token.json"

        # Load OAuth credentials safely from JSON (never pickle.load).
        if os.path.exists(token_file):
            self.creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                self.creds = flow.run_local_server(port=0, open_browser=False)

            with open(token_file, "w", encoding="utf-8") as token:
                token.write(self.creds.to_json())

        # Connect to the API service
        self.service = build('drive', 'v3', credentials=self.creds)

    def listFolders(self):
        try:
            page_token = None
            folders = []
            while True:
                response = self.service.files().list(q="mimeType='application/vnd.google-apps.folder'",
                                                     spaces='drive',
                                                     fields='nextPageToken, files(id, name)',
                                                     pageToken=page_token).execute()

                for file in response.get('files', []):
                    # Process change
                    folder_data = (file.get('name'), file.get('id'))
                    folders.append(folder_data)

                page_token = response.get('nextPageToken', None)
                if page_token is None:
                    break

            return folders
        except Exception as e:
            print(str(e))
            return None
