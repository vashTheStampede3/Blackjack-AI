"""Open the existing region editor on a user-chosen local video frame."""
import argparse
import tkinter as tk

import cv2

from lib.interfaces.video_frame_picker import VideoFramePicker
from lib.logic.card_layout import MODE_LABELS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", help="Optional local video to open")
    parser.add_argument("--profile", default="Video", help="Profile name; editable in the window")
    parser.add_argument("--mode", choices=list(MODE_LABELS), help="Initial layout mode; editable in the window")
    args = parser.parse_args()
    cv2.setNumThreads(2)
    root = tk.Tk()
    root.withdraw()
    picker = VideoFramePicker(root, video_path=args.video, profile=args.profile, on_close=root.destroy)
    if args.mode:
        picker.mode_var.set(MODE_LABELS[args.mode])
        picker._mode_changed()
    root.mainloop()


if __name__ == "__main__":
    main()
