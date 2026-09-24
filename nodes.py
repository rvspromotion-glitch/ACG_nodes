import json, os, re

SEP = " · "
NEG = re.compile(r"\b(no|not|without|avoid|absent|never)\b", re.I)
HERE = os.path.dirname(__file__)


class ACGScriptSystemPrompt:
    """Fills script_system_template.md -> string for Ask Gemini's system_instruction."""
    @classmethod
    def INPUT_TYPES(cls):
        lv = {f"sx{i}_rules": ("STRING", {"multiline": True, "default": d}) for i, d in [
            (1, "Clothed and flirty. Covered teaser, fully dressed outfit family."),
            (2, "Lingerie or swimwear outfit family, suggestive poses, everything covered."),
            (3, "Partial or implied: sheer fabric, topless from behind, arms or hands covering, back to camera."),
            (4, ""), (5, ""), (6, ""), (7, "")]}
        return {"required": {
            "trigger": ("STRING", {"default": "3lm1ra"}),
            "fixed_traits": ("STRING", {"multiline": True, "default":
                "Long platinum blond hair, grey eyes. Refer to her as `3lm1ra`. She is the only person in the image.\n"
                "When a phone is visible, it is always a white iPhone 16 Pro in a plain white case.\nAny cap or hat is plain."}),
            "images_per_bundle": ("INT", {"default": 4, "min": 1, "max": 12}),
            **lv}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("system_prompt",)
    FUNCTION = "run"
    CATEGORY = "ACG/script"

    def run(self, trigger, fixed_traits, images_per_bundle, **rules):
        lv = [i for i in range(1, 8) if rules[f"sx{i}_rules"].strip()]
        t = open(os.path.join(HERE, "script_system_template.md"), encoding="utf-8").read()
        for k, v in {
            "TRIGGER": trigger, "FIXED_TRAITS": fixed_traits.strip(),
            "LEVEL_RULES": "\n".join(f"- Level {i}: {rules[f'sx{i}_rules'].strip()}" for i in lv),
            "LEVEL_LIST": ", ".join(map(str, lv)), "BUNDLE_COUNT": str(len(lv)),
            "IMAGES_PER_BUNDLE": str(images_per_bundle)}.items():
            t = t.replace("{{" + k + "}}", v)
        return (t,)


def parse_script(text):
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    s = json.loads(text[text.find("{"): text.rfind("}") + 1])
    for k in ("environment", "lighting", "camera"):
        if not s.get("shoot", {}).get(k):
            raise ValueError(f"script missing shoot.{k}")
    return s


def unique_name(name, root):
    """black lace -> black lace b -> black lace c ... (letters, a trailing number would read as a step)"""
    import folder_paths, string
    base = os.path.join(folder_paths.get_output_directory(), root)
    cand, i = name, 1
    while os.path.exists(os.path.join(base, cand)):
        i += 1
        n, suf = i - 1, ""
        while n:
            n, r = divmod(n - 1, 26) if suf else divmod(n, 26)
            suf = string.ascii_lowercase[r] + suf
        cand = f"{name} {suf}"
    return cand


class ACGScriptParse:
    """Gemini script JSON -> lists (prompt, seed, folder, media name, caption). Downstream runs once per item."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "script_text": ("STRING", {"forceInput": True}),
            "prefix": ("STRING", {"default": "Famegrid"}),
            "base_seed": ("INT", {"default": 873447843474139, "min": 0, "max": 0xffffffffffffffff}),
            "only_levels": ("STRING", {"default": "", "tooltip": "e.g. 1,3 . empty = all"}),
            "only_image": ("INT", {"default": 0, "min": 0, "max": 12, "tooltip": "0 = all, 2 = only image 2 of each bundle"}),
            "root": ("STRING", {"default": "acg_sets", "tooltip": "same as ACG Vault Save root"}),
            "if_exists": (["new name", "overwrite"],),
            "set_name_override": ("STRING", {"default": "", "tooltip": "empty = use Gemini's set_name"}),
        }}

    @classmethod
    def IS_CHANGED(cls, **kw):
        return float("nan")  # always re-run so each queue gets a fresh, unused set name

    RETURN_TYPES = ("STRING", "INT", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "seed", "folder", "media_name", "caption", "report")
    OUTPUT_IS_LIST = (True, True, True, True, True, False)
    FUNCTION = "run"
    CATEGORY = "ACG/script"

    def run(self, script_text, prefix, base_seed, only_levels, only_image,
            root="acg_sets", if_exists="new name", set_name_override=""):
        s = parse_script(script_text)
        levels = {int(x) for x in only_levels.split(",") if x.strip()} or None
        tail = " ".join(s["shoot"][k].strip() for k in ("environment", "lighting", "camera"))
        name = (set_name_override or s.get("set_name", "untitled")).strip().lower()
        if if_exists == "new name":
            name = unique_name(name, root)
        out = [[] for _ in range(5)]
        report = [f"set: {name}"]
        for b in s["bundles"]:
            L = int(b["level"])
            report.append(f"SX{L}: {len(b['images'])} images | {b.get('outfit_family', '')}")
            if levels and L not in levels:
                continue
            for i, im in enumerate(b["images"], 1):
                if only_image and i != only_image:
                    continue
                p = im["prompt"].strip()
                if NEG.search(p):
                    report.append(f"  warn SX{L} #{i}: negation '{NEG.search(p).group()}'")
                for lst, v in zip(out, [
                        f"{prefix}, {p} {tail}" if prefix else f"{p} {tail}",
                        base_seed + L * 1000 + i,
                        f"SX{L}{SEP}{name}{SEP}{L}",
                        f"{name} sx{L}{SEP}{i}",
                        im.get("caption", "")]):
                    lst.append(v)
        if not out[0]:
            raise ValueError("nothing selected, check only_levels / only_image")
        report.append(f"rendering {len(out[0])} images")
        return (*out, "\n".join(report))


class ACGVaultSave:
    """Saves with clean vault names (no _00001_) + appends caption to manifest.json."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "folder": ("STRING", {"forceInput": True}),
            "media_name": ("STRING", {"forceInput": True}),
            "caption": ("STRING", {"forceInput": True}),
            "root": ("STRING", {"default": "acg_sets"}),
            "format": (["png", "jpg"],),
        }}

    RETURN_TYPES = ()
    OUTPUT_NODE = True
    FUNCTION = "run"
    CATEGORY = "ACG/script"

    def run(self, images, folder, media_name, caption, root, format):
        import numpy as np
        from PIL import Image
        import folder_paths
        set_name = folder.split(SEP)[1] if SEP in folder else "untitled"
        base = os.path.join(folder_paths.get_output_directory(), root, set_name)
        d = os.path.join(base, folder)
        os.makedirs(d, exist_ok=True)
        results = []
        for n, img in enumerate(images):
            arr = np.clip(255.0 * img.cpu().numpy(), 0, 255).astype(np.uint8)
            fname = media_name + (f" {n+1}" if len(images) > 1 else "") + "." + format
            Image.fromarray(arr).save(os.path.join(d, fname), quality=95)
            results.append({"filename": fname, "subfolder": os.path.relpath(d, folder_paths.get_output_directory()), "type": "output"})
        mpath = os.path.join(base, "manifest.json")
        m = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {}
        m[f"{folder}/{media_name}"] = caption
        json.dump(m, open(mpath, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        return {"ui": {"images": results}}


NODE_CLASS_MAPPINGS = {
    "ACGScriptSystemPrompt": ACGScriptSystemPrompt,
    "ACGScriptParse": ACGScriptParse,
    "ACGVaultSave": ACGVaultSave,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ACGScriptSystemPrompt": "ACG Script System Prompt",
    "ACGScriptParse": "ACG Script Parse (list)",
    "ACGVaultSave": "ACG Vault Save",
}
