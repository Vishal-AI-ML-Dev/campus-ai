for path in ("/workspace/run/campus-marker", "/workspace/campus-marker"):
    try:
        with open(path, "w") as f:
            f.write("marker")
    except OSError:
        pass
print("written")
