"""Scheduled-task entry point; never launches the Codex desktop application."""
import argparse
from pathlib import Path
import runtime_support

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--home',type=Path,default=runtime_support.ROOT)
    args=parser.parse_args();runtime_support.configure()
    from monitor import Monitor
    Monitor(args.home.resolve()).run()

if __name__=='__main__':main()
