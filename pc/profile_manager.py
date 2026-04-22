from __future__ import annotations
import json
import os
import re
from pathlib import Path


class ProfileManager:
    def __init__(self, path: str) -> None:
        self._path = path
        self.profiles: list[dict] = []

    def load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.profiles = data.get("profiles", [])
        except FileNotFoundError:
            self.profiles = []
        except json.JSONDecodeError:
            bak = self._path + ".bak"
            os.replace(self._path, bak)
            print(f"profiles.json was corrupt — backed up to {bak}, starting fresh.")
            self.profiles = []

    def save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"profiles": self.profiles}, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)

    def get_names(self) -> list[str]:
        return [p["name"] for p in self.profiles]

    def get_profile(self, name: str) -> dict | None:
        for p in self.profiles:
            if p["name"] == name:
                return p
        return None

    def add_profile(self, name: str, info: dict | None = None) -> dict:
        profile: dict = {"name": name, "info": info or {}, "calibration": {}}
        self.profiles.append(profile)
        self.save()
        return profile

    def update_calibration(self, name: str, calibration: dict) -> None:
        for p in self.profiles:
            if p["name"] == name:
                p["calibration"] = calibration
                break
        self.save()

    def update_info(self, name: str, info: dict) -> None:
        for p in self.profiles:
            if p["name"] == name:
                p["info"] = info
                break
        self.save()


def select_profile_interactive(manager: ProfileManager) -> dict:
    names = manager.get_names()

    if not names:
        print("\nNo profiles found. Let's create one.")
        name = _prompt_name()
        info = _prompt_profile_info()
        return manager.add_profile(name, info)

    while True:
        print("\n=== Select Profile ===")
        print("  0: Create new profile")
        for i, name in enumerate(names, start=1):
            print(f"  {i}: {name}")

        try:
            choice = int(input("Enter number: ").strip())
        except (ValueError, EOFError):
            print("Invalid input. Enter a number.")
            continue

        if choice == 0:
            name = _prompt_name()
            info = _prompt_profile_info()
            return manager.add_profile(name, info)
        elif 1 <= choice <= len(names):
            return manager.profiles[choice - 1]
        else:
            print(f"Please enter a number between 0 and {len(names)}.")


def _prompt_name() -> str:
    while True:
        name = input("Enter profile name: ").strip()
        if name:
            return name
        print("Name cannot be empty.")


def _prompt_profile_info() -> dict:
    print("Enter profile details (press Enter to skip any field).")

    # Sex
    while True:
        raw = input("  Sex (M/F/Other): ").strip()
        if not raw:
            sex: str | None = None
            break
        if raw.upper() in ("M", "F", "OTHER"):
            sex = raw.upper()
            break
        print("  Please enter M, F, or Other.")

    # Age
    while True:
        raw = input("  Age (years): ").strip()
        if not raw:
            age: int | None = None
            break
        try:
            age = int(raw)
            if age > 0:
                break
            print("  Age must be a positive number.")
        except ValueError:
            print("  Please enter a whole number.")

    # Weight
    while True:
        raw = input("  Weight (kg): ").strip()
        if not raw:
            weight: float | None = None
            break
        try:
            weight = float(raw)
            if weight > 0:
                break
            print("  Weight must be a positive number.")
        except ValueError:
            print("  Please enter a number.")

    # Height
    while True:
        raw = input("  Height (cm): ").strip()
        if not raw:
            height: float | None = None
            break
        try:
            height = float(raw)
            if height > 0:
                break
            print("  Height must be a positive number.")
        except ValueError:
            print("  Please enter a number.")

    info: dict = {}
    if sex    is not None: info["sex"]    = sex
    if age    is not None: info["age"]    = age
    if weight is not None: info["weight_kg"] = weight
    if height is not None: info["height_cm"] = height
    return info


def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:32]
    return sanitized if sanitized else "user"
