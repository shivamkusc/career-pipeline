import os
import subprocess
import shutil

def build_pdf(tex_path, output_folder):
    """
    Compiles a .tex file into a .pdf using pdflatex.
    """
    print(f"🔨 Compiling PDF from: {tex_path}")
    
    # Ensure absolute paths
    tex_path = os.path.abspath(tex_path)
    output_folder = os.path.abspath(output_folder)
    
    # The command to run pdflatex
    # We run it in 'interaction=nonstopmode' so it doesn't freeze on errors
    cmd = [
        "pdflatex",
        "-output-directory", output_folder,
        "-interaction=nonstopmode",
        tex_path
    ]
    
    try:
        # Run the command
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if result.returncode != 0:
            print("❌ PDF Compilation Failed!")
            # Print the last few lines of the log for debugging
            print(result.stdout.decode('latin-1')[-500:])
            return False
            
        print("✅ PDF Compiled Successfully!")
        
        # Cleanup: Remove the messy .aux and .log files
        base_name = os.path.splitext(os.path.basename(tex_path))[0]
        extensions_to_remove = ['.aux', '.log', '.out']
        
        for ext in extensions_to_remove:
            temp_file = os.path.join(output_folder, base_name + ext)
            if os.path.exists(temp_file):
                os.remove(temp_file)
                
        return True

    except FileNotFoundError:
        print("❌ Error: 'pdflatex' command not found. Do you have LaTeX installed?")
        return False


def build_cover_letter_pdf(text: str, output_folder: str, filename: str = "cover_letter", dark_mode: bool = False):
    """Wrap plain text cover letter in LaTeX and compile to PDF."""
    import re
    from datetime import date

    # Replace [Date] placeholder with today's date
    today = date.today().strftime("%B %d, %Y")
    text = text.replace("[Date]", today)

    # Escape special LaTeX characters
    def escape_latex(s):
        replacements = [
            ('\\', '\\textbackslash{}'),
            ('&', '\\&'), ('%', '\\%'), ('$', '\\$'),
            ('#', '\\#'), ('_', '\\_'),
            ('~', '\\textasciitilde{}'), ('^', '\\textasciicircum{}'),
        ]
        for char, repl in replacements:
            s = s.replace(char, repl)
        return s

    escaped = escape_latex(text)

    # Convert **bold** markdown to \textbf{}
    escaped = re.sub(r'\*\*(.+?)\*\*', r'\\textbf{\1}', escaped)

    # Parse the cover letter into structured parts
    lines = [line.strip() for line in escaped.split('\n')]

    # Find the sign-off line (e.g. "Best regards, Shivam Kumar" or "Sincerely, Name")
    signoff_patterns = ['Best regards,', 'Sincerely,', 'Best,', 'Regards,', 'Warm regards,', 'Thank you,']
    signoff_line = None
    signoff_idx = None
    for i, line in enumerate(lines):
        for pattern in signoff_patterns:
            if line.startswith(escape_latex(pattern)):
                signoff_line = line
                signoff_idx = i
                break
        if signoff_idx is not None:
            break

    # Build body paragraphs (everything before sign-off)
    body_lines = lines[:signoff_idx] if signoff_idx else lines
    # Build paragraphs separated by blank lines
    paragraphs = []
    current = []
    for line in body_lines:
        if not line:
            if current:
                paragraphs.append(' '.join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(' '.join(current))

    body = '\n\n'.join(paragraphs)

    # Build sign-off block
    signoff_tex = ""
    if signoff_idx is not None:
        # Get all lines from sign-off onward
        signoff_parts = [l for l in lines[signoff_idx:] if l]
        if len(signoff_parts) >= 2:
            # "Best regards," and "Name" on separate lines
            signoff_tex = f"\\vspace{{1em}}\n{signoff_parts[0]}\\\\\n" + "\\\\\n".join(signoff_parts[1:])
        elif signoff_parts:
            # Sign-off and name on same line — split at comma
            parts = signoff_parts[0].split(',', 1)
            if len(parts) == 2:
                signoff_tex = f"\\vspace{{1em}}\n{parts[0].strip()},\\\\\n{parts[1].strip()}"
            else:
                signoff_tex = f"\\vspace{{1em}}\n{signoff_parts[0]}"

    dark_mode_tex = ""
    if dark_mode:
        dark_mode_tex = "\\usepackage{xcolor}\n\\pagecolor[HTML]{1a1a2e}\n\\color[HTML]{e0e0e0}"

    template = f"""\\documentclass[11pt,letterpaper]{{article}}
\\usepackage[top=1in, bottom=1in, left=1in, right=1in]{{geometry}}
\\usepackage{{parskip}}
\\usepackage[hidelinks]{{hyperref}}
\\usepackage[T1]{{fontenc}}
{dark_mode_tex}
\\pagestyle{{empty}}

\\begin{{document}}

{body}

{signoff_tex}

\\end{{document}}
"""
    os.makedirs(output_folder, exist_ok=True)
    tex_path = os.path.join(output_folder, f"{filename}.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(template)

    success = build_pdf(tex_path, output_folder)

    # Clean up .tex source after successful build
    if success and os.path.exists(tex_path):
        os.remove(tex_path)

    return success