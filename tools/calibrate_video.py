"""Open the existing region editor on a user-chosen local video frame."""
import argparse
import tkinter as tk

from lib.interfaces.video_frame_picker import VideoFramePicker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", help="Optional local video to open")
    parser.add_argument("--profile", default="Video", help="Profile name; editable in the window")
    args = parser.parse_args()
    root = tk.Tk()
    root.withdraw()
    VideoFramePicker(root, video_path=args.video, profile=args.profile, on_close=root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
