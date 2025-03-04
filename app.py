import os
import sys
from dotenv import load_dotenv
import json
import hashlib
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
import asyncio
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QPushButton, QListWidget, QFileDialog, 
                            QLabel, QLineEdit,QInputDialog, QProgressBar, QMessageBox, QTreeView)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QModelIndex
from PyQt5.QtGui import QStandardItemModel, QStandardItem

load_dotenv()

class FileUploader(QThread):
    progress_signal = pyqtSignal(int)
    complete_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)
    
    def __init__(self, client, chat, file_path, loop):
        super().__init__()
        self.client = client
        self.chat = chat
        self.file_path = file_path
        self.loop = loop
        
    async def upload_file(self):
        try:
            file_name = os.path.basename(self.file_path)
            file_size = os.path.getsize(self.file_path)
            
            # Calculate file hash for identification
            sha256_hash = hashlib.sha256()
            with open(self.file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            file_hash = sha256_hash.hexdigest()
            
            # Upload with progress callback
            def progress_callback(current, total):
                percentage = int(current * 100 / total)
                self.progress_signal.emit(percentage)
            
            caption = json.dumps({
                "file_name": file_name,
                "original_path": self.file_path,
                "upload_date": datetime.now().isoformat(),
                "file_size": file_size,
                "file_hash": file_hash
            })

            message = await self.client.send_file(
                self.chat, 
                self.file_path,
                caption=caption,
                progress_callback=progress_callback
            )
            
            file_info = {
                "file_name": file_name,
                "message_id": message.id,
                "file_size": file_size,
                "upload_date": datetime.now().isoformat(),
                "file_hash": file_hash
            }
            
            self.complete_signal.emit(file_info)
        
        except Exception as e:
            self.error_signal.emit(str(e))
    
    def run(self):
        self.loop.run_until_complete(self.upload_file())


class FileDownloader(QThread):
    progress_signal = pyqtSignal(int)
    complete_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    
    def __init__(self, client, chat, message_id, save_path, loop):
        super().__init__()
        self.client = client
        self.chat = chat
        self.message_id = message_id
        self.save_path = save_path
        self.loop = loop
        
    async def download_file(self):
        try:
            message = await self.client.get_messages(self.chat, ids=self.message_id)
            
            if not message or not message.media:
                self.error_signal.emit("No file found in this message")
                return
            
            # Download with progress callback
            def progress_callback(current, total):
                percentage = int(current * 100 / total)
                self.progress_signal.emit(percentage)
            
            path = await message.download_media(
                self.save_path,
                progress_callback=progress_callback
            )
            
            self.complete_signal.emit(path)
        
        except Exception as e:
            self.error_signal.emit(str(e))
    
    def run(self):
        self.loop.run_until_complete(self.download_file())


class TelegramStorage(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api_id = os.getenv('API_ID')
        self.api_hash = os.getenv('API_HASH')
        self.client = None
        self.storage_chat = None
        self.db_file = "telegram_storage.json"
        self.files_db = self.load_db()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("TeleStore")
        self.setGeometry(100, 100, 900, 600)
        
        # Main widget and layout
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        
        # Authentication section
        auth_layout = QHBoxLayout()
        self.api_id_input = QLineEdit()
        self.api_id_input.setPlaceholderText("API ID")
        self.api_hash_input = QLineEdit()
        self.api_hash_input.setPlaceholderText("API Hash")
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_to_telegram)
        
        auth_layout.addWidget(QLabel("API ID:"))
        auth_layout.addWidget(self.api_id_input)
        auth_layout.addWidget(QLabel("API Hash:"))
        auth_layout.addWidget(self.api_hash_input)
        auth_layout.addWidget(self.connect_btn)
        
        # Storage chat selection
        chat_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Chat ID or Username (e.g: 'me' for Saved Messages)")
        self.set_chat_btn = QPushButton("Set Storage Chat")
        self.set_chat_btn.clicked.connect(self.set_storage_chat)
        
        chat_layout.addWidget(QLabel("Storage Chat:"))
        chat_layout.addWidget(self.chat_input)
        chat_layout.addWidget(self.set_chat_btn)
        
        # File browser and operations
        files_layout = QHBoxLayout()
        
        # File tree structure
        self.folder_model = QStandardItemModel()
        self.folder_model.setHorizontalHeaderLabels(['Files & Folders'])
        self.folder_view = QTreeView()
        self.folder_view.setModel(self.folder_model)
        self.folder_view.clicked.connect(self.on_folder_clicked)
        
        # File list
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(self.download_selected_file)
        
        files_layout.addWidget(self.folder_view, 1)
        files_layout.addWidget(self.file_list, 2)
        
        # Operation buttons
        btn_layout = QHBoxLayout()
        self.upload_btn = QPushButton("Upload Files")
        self.upload_btn.clicked.connect(self.upload_files)
        self.download_btn = QPushButton("Download Selected")
        self.download_btn.clicked.connect(self.download_selected_file)
        self.create_folder_btn = QPushButton("Create Folder")
        self.create_folder_btn.clicked.connect(self.create_folder)
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_selected)
        
        btn_layout.addWidget(self.upload_btn)
        btn_layout.addWidget(self.download_btn)
        btn_layout.addWidget(self.create_folder_btn)
        btn_layout.addWidget(self.delete_btn)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        
        # Add layouts to main layout
        main_layout.addLayout(auth_layout)
        main_layout.addLayout(chat_layout)
        main_layout.addLayout(files_layout)
        main_layout.addLayout(btn_layout)
        main_layout.addWidget(self.progress_bar)
        
        # Set the main layout
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        
        # Initial UI state
        self.disable_storage_features()
        
    def disable_storage_features(self):
        self.chat_input.setEnabled(False)
        self.set_chat_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.create_folder_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.folder_view.setEnabled(False)
        self.file_list.setEnabled(False)
        
    def enable_chat_selection(self):
        self.chat_input.setEnabled(True)
        self.set_chat_btn.setEnabled(True)
        
    def enable_storage_features(self):
        self.upload_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.create_folder_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        self.folder_view.setEnabled(True)
        self.file_list.setEnabled(True)
        self.update_file_tree()
        
    def connect_to_telegram(self):
        try:
            self.api_id = os.getenv('API_ID')
            self.api_hash = os.getenv('API_HASH')
            
            if not self.api_id or not self.api_hash:
                QMessageBox.warning(self, "Input Error", "Please enter API ID and API Hash")
                return
            
            # Create client connection thread
            self.connection_thread = ConnectionThread(self.api_id, self.api_hash, self.loop)
            self.connection_thread.connected_signal.connect(self.on_connected)
            self.connection_thread.error_signal.connect(self.show_error)
            self.connection_thread.start()
            
            self.connect_btn.setEnabled(False)
            self.connect_btn.setText("Connecting...")
            
        except ValueError:
            QMessageBox.warning(self, "Input Error", "API ID must be a number")
            
    def on_connected(self, client):
        self.client = client
        self.connect_btn.setText("Connected")
        QMessageBox.information(self, "Success", "Connected to Telegram")
        self.enable_chat_selection()
        
    def set_storage_chat(self):
        chat_id = self.chat_input.text()
        print(chat_id, "CHAT")
        if not chat_id:
            QMessageBox.warning(self, "Input Error", "Please enter a chat ID or username")
            return
            
        # Try to get chat in a thread
        self.chat_thread = ChatSetupThread(self.client, chat_id, self.loop)
        self.chat_thread.success_signal.connect(self.on_chat_set)
        self.chat_thread.error_signal.connect(self.show_error)
        self.chat_thread.start()
        
        self.set_chat_btn.setEnabled(False)
        self.set_chat_btn.setText("Setting chat...")
        
    def on_chat_set(self, chat):
        self.storage_chat = chat
        print(chat, "chat")
        self.set_chat_btn.setText("Chat Set")
        self.chat_input.setEnabled(False)
        QMessageBox.information(self, "Success", f"Storage chat set to {chat.title if hasattr(chat, 'title') else chat.id}")
        self.enable_storage_features()
        
    def upload_files(self):
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Select Files to Upload")
        
        if not file_paths:
            return
            
        for file_path in file_paths:
            self.upload_file(file_path)
            
    def upload_file(self, file_path):
        # Create and run uploader thread
        self.uploader = FileUploader(self.client, self.storage_chat, file_path, self.loop)
        print(self.client, self.storage_chat, "INFO")
        self.uploader.progress_signal.connect(self.update_progress)
        self.uploader.complete_signal.connect(self.on_upload_complete)
        self.uploader.error_signal.connect(self.show_error)
        self.uploader.start()
        
        self.disable_buttons_during_operation()
        
    def on_upload_complete(self, file_info):
        # Add file to database
        self.files_db.append(file_info)
        self.save_db()
        self.update_file_tree()
        self.reset_ui_after_operation()
        QMessageBox.information(self, "Upload Complete", f"File {file_info['file_name']} uploaded successfully")
        
    def download_selected_file(self):
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "Please select a file to download")
            return
            
        file_name = selected_items[0].text()
        file_info = None
        
        for file in self.files_db:
            if file["file_name"] == file_name:
                file_info = file
                break
                
        if not file_info:
            QMessageBox.warning(self, "File Error", "File information not found")
            return
            
        save_path, _ = QFileDialog.getSaveFileName(self, "Save File", file_name)
        
        if not save_path:
            return
            
        # Create and run downloader thread
        self.downloader = FileDownloader(self.client, self.storage_chat, file_info["message_id"], save_path, self.loop)
        self.downloader.progress_signal.connect(self.update_progress)
        self.downloader.complete_signal.connect(self.on_download_complete)
        self.downloader.error_signal.connect(self.show_error)
        self.downloader.start()
        
        self.disable_buttons_during_operation()
        
    def on_download_complete(self, file_path):
        self.reset_ui_after_operation()
        QMessageBox.information(self, "Download Complete", f"File downloaded to {file_path}")
        
    def create_folder(self):
        # This is a virtual folder system since Telegram doesn't have folders
        folder_name, ok = QInputDialog.getText(self, "Create Folder", "Folder Name:")
        
        if ok and folder_name:
            root_item = self.folder_model.invisibleRootItem()
            folder_item = QStandardItem(folder_name)
            folder_item.setEditable(False)
            root_item.appendRow(folder_item)
            
    def delete_selected(self):
        # Virtual deletion - we're just removing from our database
        selected_items = self.file_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "Please select a file to delete")
            return
            
        file_name = selected_items[0].text()
        
        for i, file in enumerate(self.files_db):
            if file["file_name"] == file_name:
                del self.files_db[i]
                self.save_db()
                break
                
        self.update_file_tree()
        QMessageBox.information(self, "File Removed", f"File {file_name} removed from database")
    
    def on_folder_clicked(self, index):
        # When a folder is clicked here, update the file listt!
        item = self.folder_model.itemFromIndex(index)
        
        if item:
            # Clearing the file list
            self.file_list.clear()

            for file in self.files_db:
                self.file_list.addItem(file["file_name"])
    
    def update_file_tree(self):
        # Update the file tree with folders and files
        # For now, we'll just display all files in the root
        root_item = self.folder_model.invisibleRootItem()
        
        # Clear existing items
        root_item.removeRows(0, root_item.rowCount())
        
        # Add a "All Files" item
        all_files_item = QStandardItem("All Files")
        all_files_item.setEditable(False)
        root_item.appendRow(all_files_item)
        
        # Add files to list view when "All Files" is selected
        self.file_list.clear()
        for file in self.files_db:
            self.file_list.addItem(file["file_name"])
    
    def update_progress(self, value):
        self.progress_bar.setValue(value)
    
    def disable_buttons_during_operation(self):
        self.upload_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
    
    def reset_ui_after_operation(self):
        self.progress_bar.setValue(0)
        self.upload_btn.setEnabled(True)
        self.download_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
    
    def show_error(self, error_message):
        QMessageBox.critical(self, "Error", error_message)
        self.reset_ui_after_operation()
    
    def load_db(self):
        try:
            if os.path.exists(self.db_file):
                with open(self.db_file, 'r') as f:
                    return json.load(f)
            return []
        except Exception as e:
            print(f"Error loading database: {e}")
            return []
    
    def save_db(self):
        try:
            with open(self.db_file, 'w') as f:
                json.dump(self.files_db, f)
        except Exception as e:
            print(f"Error saving database: {e}")


class ConnectionThread(QThread):
    connected_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)
    
    def __init__(self, api_id, api_hash, loop):
        super().__init__()
        self.api_id = api_id
        self.api_hash = api_hash
        self.loop = loop
        
    async def connect_client(self):
        try:
            # Create the client
            client = TelegramClient('telegram_storage_session', self.api_id, self.api_hash)
            
            # Connect and ensure authorized
            await client.start()
            
            if not await client.is_user_authorized():
                QMessageBox.information(None, "Authentication", "Please check your phone for the code and enter it here.")
                
            
            self.connected_signal.emit(client)
            
        except Exception as e:
            self.error_signal.emit(str(e))
    
    def run(self):
        self.loop.run_until_complete(self.connect_client())


class ChatSetupThread(QThread):
    success_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)
    
    def __init__(self, client, chat_id, loop):
        super().__init__()
        self.client = client
        self.chat_id = chat_id
        self.loop = loop
        
    async def setup_chat(self):
        try:
            # Try to get the chat entity
            chat = await self.client.get_entity(self.chat_id)
            self.success_signal.emit(chat)
            
        except Exception as e:
            self.error_signal.emit(str(e))
    
    def run(self):
        self.loop.run_until_complete(self.setup_chat())


def main():
    app = QApplication(sys.argv)
    window = TelegramStorage()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()