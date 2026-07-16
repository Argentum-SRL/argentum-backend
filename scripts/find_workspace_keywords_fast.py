import os

search_dir = r"C:\Users\sebas\OneDrive\Documentos\GitHub"
found = []
ignore_dirs = {".venv", "venv", ".git", "node_modules", "__pycache__"}

for root, dirs, files in os.walk(search_dir):
    # modify dirs in place to ignore specific directories
    dirs[:] = [d for d in dirs if d not in ignore_dirs]
    for file in files:
        if file.endswith((".md", ".txt", ".xml", ".py")):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if "MAD" in content or "conservador" in content or "inusual" in content:
                        found.append(path)
            except Exception:
                pass

print("Matching files in workspace:")
for f in found:
    print(f)
