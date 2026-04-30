import os
import sys

def convert_pdf_to_png(pdf_path, output_dir):
    try:
        from pdf2image import convert_from_path
        # Windows 环境下通常需要 poppler 库配置在环境变量，或者通过 poppler_path 指定
        # 但是由于不确定环境里是否配好了，先尝试直接转，如果失败则输出提示。
        images = convert_from_path(pdf_path, dpi=150)
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        
        saved_paths = []
        for i, img in enumerate(images):
            out_img = os.path.join(output_dir, f"{base_name}_page_{i+1}.png")
            img.save(out_img, "PNG")
            saved_paths.append(out_img)
        print(f"🌄 Converted {base_name} to {len(saved_paths)} images.")
        return saved_paths
    except Exception as e:
        print(f"❌ Failed to convert PDF to Image (Please ensure poppler is installed): {e}")
        return []

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python pdf_to_img.py <pdf_path> <output_dir>")
        sys.exit(1)
        
    convert_pdf_to_png(sys.argv[1], sys.argv[2])
