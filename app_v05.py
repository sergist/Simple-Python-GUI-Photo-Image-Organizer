import os
import sys
import shutil
import datetime
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter import ttk
from PIL import Image, ExifTags
import exifread
import cr3_parser

# Supported file extensions for JPEG and RAW images
JPEG_EXTENSIONS = ('.jpg', '.jpeg')
RAW_EXTENSIONS = ('.cr2', '.cr3', '.nef', '.arw', '.dng', '.raw', '.rw2')

def get_exiftool_path():
    """
    Returns the path to exiftool. When the app is bundled with PyInstaller,
    exiftool.exe is assumed to be included in the _MEIPASS directory.
    """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'exiftool.exe')
    else:
        return 'exiftool'  # Assumes exiftool is available in the PATH

def get_date_taken(file_path, file_type):
    """
    Attempts to extract the date when the photo was taken from the file's EXIF data.
    Falls back to the file's modification time if no EXIF date is found.
    For CR3 (Canon RAW) files, uses exiftool.
    """
    date_taken = None

    if file_type == "jpeg":
        try:
            with Image.open(file_path) as img:
                exif_data = img._getexif()
                if exif_data:
                    for tag, value in exif_data.items():
                        tag_name = ExifTags.TAGS.get(tag, tag)
                        if tag_name == "DateTimeOriginal":
                            date_taken = datetime.datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                            break
        except Exception as e:
            print(f"Error reading EXIF data from JPEG '{file_path}': {e}")

    elif file_type == "raw":
        if file_path.lower().endswith('.cr3'):
            metadata = cr3_parser.extract_cr3_metadata(file_path)
            if metadata.get("date_taken"):
                return datetime.datetime.strptime(metadata["date_taken"], '%Y-%m-%d %H:%M:%S')
        else:
            try:
                with open(file_path, 'rb') as f:
                    tags = exifread.process_file(f, stop_tag="EXIF DateTimeOriginal", details=False)
                    if "EXIF DateTimeOriginal" in tags:
                        date_str = str(tags["EXIF DateTimeOriginal"])
                        date_taken = datetime.datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
            except Exception as e:
                print(f"Error reading EXIF data from RAW '{file_path}': {e}")

    if date_taken is None:
        mod_time = os.path.getmtime(file_path)
        date_taken = datetime.datetime.fromtimestamp(mod_time)
    return date_taken

def ensure_unique_filename(dest_path):
    """
    Ensures that the destination filename is unique by appending a counter if needed.
    """
    base, extension = os.path.splitext(dest_path)
    counter = 1
    unique_path = dest_path
    while os.path.exists(unique_path):
        unique_path = f"{base}_{counter}{extension}"
        counter += 1
    return unique_path

# Updated helper function to extract focal length
def get_focal_length(file_path, file_type):
    focal_length = None
    if file_type == "jpeg":
        try:
            with Image.open(file_path) as img:
                exif_data = img._getexif()
                if exif_data:
                    for tag, value in exif_data.items():
                        tag_name = ExifTags.TAGS.get(tag, tag)
                        if tag_name == "FocalLength":
                            focal_length = value
                            break
        except Exception as e:
            print(f"Error reading EXIF FocalLength from JPEG '{file_path}': {e}")
    elif file_type == "raw":
        # For CR3 files, use exiftool as exifread might not extract the metadata correctly.
        if file_path.lower().endswith('.cr3'):
            metadata = cr3_parser.extract_cr3_metadata(file_path)
            return f"{int(float(metadata.get("focal_length", "0").split()[0]))}mm"
        else:
            try:
                with open(file_path, 'rb') as f:
                    tags = exifread.process_file(f, stop_tag="EXIF FocalLength", details=False)
                    if "EXIF FocalLength" in tags:
                        focal_tag = tags["EXIF FocalLength"]
                        focal_length = focal_tag.values[0] if hasattr(focal_tag, 'values') else None
            except Exception as e:
                print(f"Error reading EXIF FocalLength from RAW '{file_path}': {e}")
    
    if focal_length is not None:
        try:
            # If focal_length is already a float (from CR3 via exiftool) then use it directly.
            if isinstance(focal_length, (float, int)):
                focal_value = focal_length
            elif isinstance(focal_length, tuple):
                focal_value = focal_length[0] / focal_length[1] if focal_length[1] != 0 else None
            elif hasattr(focal_length, "num") and hasattr(focal_length, "denom"):
                focal_value = focal_length.num / focal_length.denom if focal_length.denom != 0 else None
            else:
                focal_value = float(focal_length)
        except Exception as e:
            print(f"Error processing focal length value for '{file_path}': {e}")
            focal_value = None
        if focal_value is not None:
            focal_value_int = round(focal_value)
            return f"{focal_value_int}mm"
    return None

def process_images(input_dir, output_dir, raw_subfolder, image_subfolder, sort_by_focal_range, action_mode, log_callback, progress_callback, done_callback):
    """
    Walks through the input directory, processes each image file, and transfers it (move or copy)
    into an organized folder structure in the output directory.
    
    Folder structure:
      output_dir/
         <year>/               # Top folder is the year
            YYYY-MM/           # Month folder (year-month)
               YYYY-MM-DD/     # Day folder (year-month-day)
                   [focal range folder]/   # Optional: folder for focal range if metadata is available
                       [raw/]      # if raw_subfolder is True and file is RAW
                       [jpeg/]     # if image_subfolder is True and file is JPEG
    
    Logs each operation and updates the progress bar.
    Calls done_callback() when complete.
    """
    files_to_process = []
    for root, dirs, files in os.walk(input_dir):
        for file in files:
            file_lower = file.lower()
            if file_lower.endswith(JPEG_EXTENSIONS) or file_lower.endswith(RAW_EXTENSIONS):
                files_to_process.append(os.path.join(root, file))
    total_files = len(files_to_process)
    log_callback(f"Found {total_files} files to process.\n")
    
    processed_count = 0
    for file_path in files_to_process:
        file_lower = file_path.lower()
        if file_lower.endswith(JPEG_EXTENSIONS):
            file_type = "jpeg"
        elif file_lower.endswith(RAW_EXTENSIONS):
            file_type = "raw"
        else:
            continue

        date_taken = get_date_taken(file_path, file_type)
        year = str(date_taken.year)
        month = f"{date_taken.month:02d}"
        day = f"{date_taken.day:02d}"
        
        month_folder = f"{year}-{month}"
        day_folder = f"{year}-{month}-{day}"
        
        dest_dir = os.path.join(output_dir, year, month_folder, day_folder)
        
        # Insert focal range folder if the option is enabled and metadata exists.
        if sort_by_focal_range:
            focal_folder = get_focal_length(file_path, file_type)
            if focal_folder:
                dest_dir = os.path.join(dest_dir, focal_folder)
        
        # Place RAW or JPEG files into a subfolder if that option is enabled.
        if file_type == "raw" and raw_subfolder:
            dest_dir = os.path.join(dest_dir, "raw")
        elif file_type == "jpeg" and image_subfolder:
            dest_dir = os.path.join(dest_dir, "jpeg")
        
        os.makedirs(dest_dir, exist_ok=True)

        dest_path = os.path.join(dest_dir, os.path.basename(file_path))
        dest_path = ensure_unique_filename(dest_path)
        try:
            if action_mode == "move":
                shutil.move(file_path, dest_path)
                log_callback(f"Moved '{file_path}' to '{dest_path}'\n")
            else:
                shutil.copy2(file_path, dest_path)
                log_callback(f"Copied '{file_path}' to '{dest_path}'\n")
        except Exception as e:
            log_callback(f"Error transferring '{file_path}' to '{dest_path}': {e}\n")
        
        processed_count += 1
        progress_callback(processed_count, total_files)
    
    log_callback("Processing complete.\n")
    done_callback()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Image Organizer v0.5 by sergist")
        self.geometry("600x550")
        
        # Input folder selection
        self.create_folder_selection("Input Folder:", self.browse_input)
        
        # Output folder selection
        self.create_folder_selection("Output Folder:", self.browse_output)
        
        # Radio buttons for mode selection (move or copy)
        self.action_mode = tk.StringVar(value="move")
        tk.Label(self, text="Action Mode:").pack(pady=5)
        tk.Radiobutton(self, text="Move", variable=self.action_mode, value="move").pack()
        tk.Radiobutton(self, text="Copy", variable=self.action_mode, value="copy").pack()
        
        # Checkboxes for subfolder options (RAW/JPEG)
        self.raw_subfolder = tk.BooleanVar()
        self.image_subfolder = tk.BooleanVar()
        self.raw_checkbox = tk.Checkbutton(self, text="Put RAW files in subfolder", variable=self.raw_subfolder)
        self.raw_checkbox.pack(pady=5)
        self.image_checkbox = tk.Checkbutton(self, text="Put Image files in subfolder", variable=self.image_subfolder)
        self.image_checkbox.pack(pady=5)
        
        # Checkbox for sorting by focal range
        self.sort_by_focal_range = tk.BooleanVar()
        self.focal_checkbox = tk.Checkbutton(self, text="Sort by Focal Range", variable=self.sort_by_focal_range)
        self.focal_checkbox.pack(pady=5)
        
        # Start processing button
        self.start_button = tk.Button(self, text="Start Processing", command=self.start_processing)
        self.start_button.pack(pady=10)
        
        # Progress bar
        self.progress = ttk.Progressbar(self, orient="horizontal", length=400, mode="determinate")
        self.progress.pack(pady=10)
        
        # Log area
        self.log_area = scrolledtext.ScrolledText(self, width=70, height=10, state="disabled")
        self.log_area.pack(pady=10)
        
    def create_folder_selection(self, label_text, browse_command):
        """Creates a folder selection label, entry, and browse button."""
        frame = tk.Frame(self, bg="#f0f0f0")
        frame.pack(pady=10)

        label = tk.Label(frame, text=label_text, bg="#f0f0f0")
        label.pack(side=tk.LEFT, padx=5)

        entry = tk.Entry(frame, width=40)
        entry.pack(side=tk.LEFT, padx=5)
        
        browse_button = tk.Button(frame, text="Browse", command=browse_command)
        browse_button.pack(side=tk.LEFT, padx=5)

        # Store the entry in the instance for later use
        if label_text == "Input Folder:":
            self.input_entry = entry
        else:
            self.output_entry = entry

    def create_radio_buttons(self, label_text, options):
        """Creates a label and radio buttons for action mode selection."""
        frame = tk.Frame(self, bg="#f0f0f0")
        frame.pack(pady=10)

        label = tk.Label(frame, text=label_text, bg="#f0f0f0")
        label.pack(side=tk.LEFT, padx=5)

        for text, value in options:
            rb = tk.Radiobutton(frame, text=text, variable=self.action_mode, value=value, bg="#f0f0f0")
            rb.pack(side=tk.LEFT, padx=5)
    
    def browse_input(self):
        folder = filedialog.askdirectory()
        if folder:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, folder)
    
    def browse_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)
    
    def log(self, message):
        self.log_area.configure(state="normal")
        self.log_area.insert(tk.END, message)
        self.log_area.see(tk.END)
        self.log_area.configure(state="disabled")
    
    def update_progress(self, processed, total):
        percentage = (processed / total) * 100
        self.progress['value'] = percentage
        self.update_idletasks()
    
    def start_processing(self):
        input_dir = self.input_entry.get().strip()
        output_dir = self.output_entry.get().strip()
        
        if os.path.abspath(input_dir) == os.path.abspath(output_dir):
            messagebox.showerror("Error", "Input and output folders cannot be the same.")
            return
        
        if not os.path.isdir(input_dir):
            messagebox.showerror("Error", "Invalid input folder")
            return
        
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        self.start_button.config(state="disabled")
        self.progress['value'] = 0
        
        def done_callback():
            self.after(0, lambda: self.start_button.config(state="normal"))
        
        threading.Thread(
            target=process_images,
            args=(
                input_dir,
                output_dir,
                self.raw_subfolder.get(),
                self.image_subfolder.get(),
                self.sort_by_focal_range.get(),
                self.action_mode.get(),
                self.log,
                self.update_progress,
                done_callback
            ),
            daemon=True
        ).start()

if __name__ == '__main__':
    app = App()
    app.mainl
   