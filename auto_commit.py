#!/usr/bin/env python3
"""
Agendino Auto-Commit Service

Automatically commits and pushes changes to the repository at regular intervals.
This helps keep your work backed up without manual intervention.
"""

import subprocess
import schedule
import time
import logging
import os
from datetime import datetime
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('auto_commit.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
REPO_PATH = Path(__file__).parent.absolute()
BRANCH = 'master'
COMMIT_INTERVAL_HOURS = 2  # Commit every 2 hours
MAX_LOG_SIZE_MB = 10  # Rotate log when it gets too large

def check_git_status():
    """Check if there are any changes to commit."""
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            cwd=REPO_PATH,
            timeout=30
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error("Git status command timed out")
        return None
    except Exception as e:
        logger.error(f"Error checking git status: {e}")
        return None

def get_changed_files():
    """Get a summary of changed files for the commit message."""
    try:
        result = subprocess.run(
            ['git', 'diff', '--name-status', 'HEAD'],
            capture_output=True,
            text=True,
            cwd=REPO_PATH,
            timeout=30
        )
        changes = result.stdout.strip().split('\n') if result.stdout.strip() else []

        # Also check for untracked files
        untracked_result = subprocess.run(
            ['git', 'ls-files', '--others', '--exclude-standard'],
            capture_output=True,
            text=True,
            cwd=REPO_PATH,
            timeout=30
        )
        untracked = untracked_result.stdout.strip().split('\n') if untracked_result.stdout.strip() else []

        summary = []
        if changes and changes[0]:  # Check if changes is not just ['']
            summary.append(f"{len(changes)} modified files")
        if untracked and untracked[0]:  # Check if untracked is not just ['']
            summary.append(f"{len(untracked)} new files")

        return summary
    except Exception as e:
        logger.error(f"Error getting changed files: {e}")
        return ["changes detected"]

def auto_commit():
    """Check for changes and commit/push if any exist."""
    logger.info("Checking for changes...")

    # Check if we're in a git repository
    if not (REPO_PATH / '.git').exists():
        logger.error(f"Not a git repository: {REPO_PATH}")
        return False

    changes = check_git_status()
    if changes is None:
        return False

    if not changes:
        logger.info("No changes to commit")
        return True

    try:
        # Add all changes
        logger.info("Adding changes to staging area...")
        subprocess.run(['git', 'add', '-A'], cwd=REPO_PATH, check=True, timeout=30)

        # Create commit message with summary
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        change_summary = get_changed_files()
        summary_text = ", ".join(change_summary) if change_summary else "updates"

        commit_message = f"Auto-commit: {summary_text} ({timestamp})"

        # Commit changes
        logger.info(f"Committing: {commit_message}")
        subprocess.run([
            'git', 'commit', '-m', commit_message
        ], cwd=REPO_PATH, check=True, timeout=60)

        # Push to remote
        logger.info(f"Pushing to origin/{BRANCH}...")
        subprocess.run([
            'git', 'push', 'origin', BRANCH
        ], cwd=REPO_PATH, check=True, timeout=120)

        logger.info("✅ Successfully committed and pushed changes")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {e}")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Git command timed out")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during commit: {e}")
        return False

def rotate_log_if_needed():
    """Rotate log file if it gets too large."""
    log_file = Path('auto_commit.log')
    if log_file.exists():
        size_mb = log_file.stat().st_size / (1024 * 1024)
        if size_mb > MAX_LOG_SIZE_MB:
            backup_name = f"auto_commit.log.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            log_file.rename(backup_name)
            logger.info(f"Log file rotated to {backup_name}")

def check_git_config():
    """Ensure git is properly configured."""
    try:
        # Check if user.name and user.email are set
        subprocess.run(['git', 'config', 'user.name'],
                      cwd=REPO_PATH, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email'],
                      cwd=REPO_PATH, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        logger.error("Git user configuration missing. Please set user.name and user.email")
        return False

def main():
    """Main service loop."""
    logger.info("=" * 60)
    logger.info("Starting Agendino Auto-Commit Service")
    logger.info(f"Repository: {REPO_PATH}")
    logger.info(f"Branch: {BRANCH}")
    logger.info(f"Commit interval: every {COMMIT_INTERVAL_HOURS} hours")
    logger.info("=" * 60)

    # Check git configuration
    if not check_git_config():
        logger.error("Git configuration check failed. Exiting.")
        return

    # Schedule the auto-commit function
    schedule.every(COMMIT_INTERVAL_HOURS).hours.do(auto_commit)

    # Also schedule log rotation daily
    schedule.every().day.at("03:00").do(rotate_log_if_needed)

    # Run initial commit check
    logger.info("Running initial commit check...")
    auto_commit()

    # Main loop
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    except KeyboardInterrupt:
        logger.info("Service stopped by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}")

if __name__ == "__main__":
    main()