from pocketbase import PocketBase  # Client also works the same
from pocketbase.client import FileUpload
import os, shutil, time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()


client = PocketBase(os.getenv("CLIENT"))

access_token = os.getenv("PBTOKEN")

def upload_media(file_path:str):

 # create record and upload file to image field
 result = client.collection("temp_storage").create(
    {
        "file": FileUpload((file_path, open(file_path, "rb"))),
    },{"pbtoken": access_token})


 record = client.collection("temp_storage").get_one(result.id)

 return client.get_file_url(record,record.file)  # get file url

#upload_media("uploads/sample.txt")  # example usage

def delete_media():
  while True:
    collection = client.collection("temp_storage").get_list(1, 50, {"pbtoken": access_token}).items
    now = datetime.now()

    for record in collection:
        if now - record.created > timedelta(hours=1):
            print(f"🧹 Deleting {record.id} ({record.created})")
            client.collection("temp_storage").delete(record.id, {"pbtoken": access_token})

    # Sleep for 1 hour before checking again        
    time.sleep(3650)          

#delete_media()
