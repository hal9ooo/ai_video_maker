import re

def escape_ass(text):
    return text.replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}').replace('\n', '\\N')

primary = "&H00FFFF&" # Cyan
karaoke_c = "&HFFFFFF&" # White

parts = []
parts.append("{\\kf100}" + escape_ass("Hello "))
parts.append("{\\kf100}" + escape_ass("World"))

print("".join(parts))
