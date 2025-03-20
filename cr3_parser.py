import exifread
import struct
from io import BytesIO
import os
from datetime import datetime

DEBUG = False

def debug_print(*args):
    if DEBUG: print("[DEBUG]", *args)

def read_box_header(file_obj):
    """Robust box header reader with error handling"""
    try:
        pos = file_obj.tell()
        size_bytes = file_obj.read(4)
        if len(size_bytes) < 4: return None, None, 0
        box_size = struct.unpack('>I', size_bytes)[0]
        box_type = file_obj.read(4)
        header_size = 8

        if box_size == 1:  # Extended size
            ext_size = file_obj.read(8)
            if len(ext_size) < 8: return None, None, 0
            box_size = struct.unpack('>Q', ext_size)[0]
            header_size += 8

        return box_type, box_size, header_size
    except Exception as e:
        debug_print(f"Header error @ {hex(pos)}: {str(e)}")
        return None, None, 0

def parse_container(data, target_boxes):
    """Recursive container parser with UUID handling"""
    cmt_boxes = {}
    io = BytesIO(data)
    
    while io.tell() < len(data):
        pos = io.tell()
        box_type, box_size, header_size = read_box_header(io)
        if not box_type: break

        content_size = box_size - header_size
        content = io.read(content_size) if content_size > 0 else b''

        # Handle Canon metadata UUID
        if box_type == b'uuid' and content.startswith(b'\x85\xc0\xb6\x87\x82\x0f\x11\xe0\x81\x11\xf4\xce\x46\x2b\x6a\x48'):
            debug_print("Found Canon metadata UUID container")
            sub_boxes = parse_container(content[16:], target_boxes)
            cmt_boxes.update(sub_boxes)
            
        elif box_type in target_boxes:
            cmt_boxes[box_type.decode()] = content
            debug_print(f"Found {box_type.decode()} box")
            
        elif box_type in [b'moov', b'trak', b'mdia', b'minf', b'udta']:
            sub_boxes = parse_container(content, target_boxes)
            cmt_boxes.update(sub_boxes)

        io.seek(pos + box_size)
        
    return cmt_boxes

def extract_exif_tags(cmt_data):
    """Corrected EXIF tag extraction with proper field names"""
    tags = {}
    for cmt_type, data in cmt_data.items():
        try:
            temp_tags = exifread.process_file(BytesIO(data), details=False)
            #for tag in temp_tags:
            #    print(f"{cmt_type} {tag}: {temp_tags[tag]}")
            
            if cmt_type == 'CMT1':
                tags.update({
                    'camera_make': str(temp_tags.get('Image Make', 'Unknown')),
                    'camera_model': str(temp_tags.get('Image Model', 'Unknown')),
                    'date_taken': str(temp_tags.get('Image DateTime', 
                                        temp_tags.get('EXIF DateTimeOriginal', 'Unknown')))
                })
                
            elif cmt_type == 'CMT2':
                tags.update({
                    'focal_length': str(temp_tags.get('Image FocalLength', 
                                           temp_tags.get('EXIF FocalLength', 'Unknown'))),
                    'exposure': str(temp_tags.get('Image ExposureTime', 
                                       temp_tags.get('EXIF ExposureTime', 'Unknown'))),
                    'aperture': str(temp_tags.get('Image FNumber', 
                                       temp_tags.get('EXIF FNumber', 'Unknown'))),
                    'iso': str(temp_tags.get('Image ISOSpeedRatings', 
                                  temp_tags.get('EXIF ISOSpeedRatings', 'Unknown')))
                })
                
            elif cmt_type == 'CMT3':
                tags.update({
                    'lens_model': str(temp_tags.get('MakerNote LensModel', 
                                         temp_tags.get('EXIF LensModel', 
                                         temp_tags.get('Image LensModel', 'Unknown'))))
                })
                
        except Exception as e:
            debug_print(f"Error processing {cmt_type}: {str(e)}")
            
    return tags
def format_metadata(raw_tags):
    """Improved metadata formatting with validation"""
    formatted = {}
    
    # Date formatting with multiple patterns
    date_str = raw_tags.get('date_taken', 'Unknown')
    for fmt in ['%Y:%m:%d %H:%M:%S', '%Y-%m-%d %H:%M:%S']:
        try:
            dt = datetime.strptime(date_str, fmt)
            formatted['date_taken'] = dt.strftime('%Y-%m-%d %H:%M:%S')
            break
        except:
            formatted['date_taken'] = date_str

    # Focal length processing
    focal_str = raw_tags.get('focal_length', 'Unknown')
    if focal_str != 'Unknown':
        try:
            # Handle different formats: "50.0 mm", "50/1", "50"
            parts = focal_str.replace('mm', '').strip().split()
            value = parts[0] if parts else ''
            
            if '/' in value:
                num, den = map(float, value.split('/'))
                formatted['focal_length'] = f"{num/den:.1f} mm"
            else:
                formatted['focal_length'] = f"{float(value)} mm"
        except:
            formatted['focal_length'] = 'Unknown'

    # Direct mappings with fallbacks
    formatted.update({
        'camera_make': raw_tags.get('camera_make', 'Unknown'),
        'camera_model': raw_tags.get('camera_model', 'Unknown'),
        'lens_model': raw_tags.get('lens_model', 'Unknown'),
        'exposure': raw_tags.get('exposure', 'Unknown'),
        'aperture': raw_tags.get('aperture', 'Unknown'),
        'iso': raw_tags.get('iso', 'Unknown')
    })
        
    return formatted

def extract_cr3_metadata(file_path):
    """Main extraction function with enhanced debugging"""
    try:
        with open(file_path, 'rb') as f:
            debug_print("\n=== Starting CR3 Analysis ===")
            
            # Locate moov container
            moov_data = None
            debug_print("Scanning for moov container...")
            while True:
                pos = f.tell()
                box_type, box_size, header_size = read_box_header(f)
                if not box_type: break

                if box_type == b'moov':
                    debug_print(f"Found moov container at {hex(pos)}")
                    moov_data = f.read(box_size - header_size)
                    break
                else:
                    f.seek(pos + box_size)

            if not moov_data:
                debug_print("No moov container found")
                return {}

            # Extract CMT boxes
            debug_print("\n=== Parsing MOOV Contents ===")
            cmt_boxes = parse_container(moov_data, [b'CMT1', b'CMT2', b'CMT3', b'CMT4'])
            debug_print(f"Found CMT boxes: {list(cmt_boxes.keys())}")

            if not cmt_boxes:
                debug_print("No CMT boxes found")
                return {}

            # Process EXIF data
            debug_print("\n=== Processing EXIF Data ===")
            raw_tags = extract_exif_tags(cmt_boxes)
            if not raw_tags:
                debug_print("No EXIF data extracted")
                return {}

            debug_print("\n=== Raw EXIF Tags ===")
            for k, v in raw_tags.items():
                debug_print(f"{k}: {v}")

            return format_metadata(raw_tags)

    except Exception as e:
        debug_print(f"Critical error: {str(e)}")
        return {}

# Usage example
#metadata = extract_cr3_metadata('305A3172.CR3')
#if metadata:
#    print(f"""
#    Camera: {metadata['camera_make']} {metadata['camera_model']}
#    Lens: {metadata['lens_model']}
#    Focal Length: {metadata['focal_length']}
#    Date Taken: {metadata['date_taken']}
#    Exposure: {metadata['exposure']}
#    Aperture: {metadata['aperture']}
#    ISO: {metadata['iso']}
#    """)
#else:
#    print("No metadata extracted")
