import sys

def modify_hydro1d():
    filepath = 'src/core/hydro1d.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Search for def compute_hydro1d_profile(...)
    search1 = """                            warnings: list[str] = None) -> tuple[list[float], list[Optional[HydroNode]]]:"""
    replace1 = """                            warnings: list[str] = None) -> tuple[list[float], list['HydroNode']]:"""

    content = content.replace(search1, replace1)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

modify_hydro1d()
