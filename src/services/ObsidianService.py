import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
import re

class ObsidianService:
    def __init__(self, vault_path: str, auto_commit_script: str = None):
        self.vault_path = Path(vault_path) if vault_path else None
        self.auto_commit_script = auto_commit_script
        if self.vault_path and self.vault_path.exists():
            self.notes_folder = self.vault_path / "Agendino"
            self.notes_folder.mkdir(exist_ok=True)
        else:
            self.notes_folder = None
    
    @property
    def is_configured(self) -> bool:
        return self.vault_path is not None and self.vault_path.exists()
    
    def _convert_action_items_to_tasks(self, markdown: str) -> str:
        """Convert action items to Obsidian checkbox tasks"""
        lines = markdown.split('\n')
        processed_lines = []
        in_action_section = False
        
        for line in lines:
            # Detect action item sections
            if re.match(r'^#+\s*(Action|Actions|Action Items|Next Steps|Tasks|To Do)', line, re.IGNORECASE):
                in_action_section = True
                processed_lines.append(line)
            elif re.match(r'^#+\s+', line):
                # New section that's not actions
                in_action_section = False
                processed_lines.append(line)
            elif in_action_section and re.match(r'^[-*]\s+', line):
                # Convert bullet points in action sections to tasks
                task_line = re.sub(r'^[-*]\s+', '- [ ] ', line)
                processed_lines.append(task_line)
            else:
                processed_lines.append(line)
        
        return '\n'.join(processed_lines)
    
    def _format_tasks_section(self, tasks: list) -> str:
        """Format Agendino tasks as Obsidian checkboxes"""
        if not tasks or len(tasks) == 0:
            return ""
        
        section = "\n## 📋 Tasks\n\n"
        for task in tasks:
            owner = task.get('owner', 'Unassigned')
            due_date = task.get('due_date', '')
            description = task.get('description', task.get('task', ''))
            priority = task.get('priority', '')
            
            # Main task checkbox
            section += f"- [ ] {description}\n"
            
            # Add metadata as sub-bullets
            if owner and owner != 'Unassigned':
                section += f"  - 👤 Owner: {owner}\n"
            if due_date:
                section += f"  - 📅 Due: {due_date}\n"
            if priority:
                section += f"  - ⚡ Priority: {priority}\n"
            section += "\n"
        
        return section
    
    def publish_summary(self, recording_name: str, title: str, tags: list, summary_markdown: str, tasks: list = None) -> dict:
        """Create a markdown note in Obsidian vault and auto-commit"""
        if not self.is_configured:
            return {"ok": False, "error": "Obsidian vault not configured"}
        
        logging.info(f"ObsidianService: auto_commit_script = {self.auto_commit_script}")
        logging.info(f"ObsidianService: script exists = {os.path.exists(self.auto_commit_script) if self.auto_commit_script else False}")
        
        try:
            # Create filename from recording name and timestamp
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            safe_title = title.replace(' ', '-').replace('/', '-').replace(':', '-').replace('\\', '-')[:50]
            filename = f"{timestamp}-{safe_title}.md"
            filepath = self.notes_folder / filename
            
            # Convert action items in summary to Obsidian tasks
            processed_summary = self._convert_action_items_to_tasks(summary_markdown)
            
            # Format generated tasks section
            tasks_section = self._format_tasks_section(tasks)
            
            # Format as markdown with YAML frontmatter
            tag_list = ', '.join(tags) if tags else 'agendino'
            content = f"""---
title: "{title}"
tags: [{tag_list}]
source: {recording_name}
created: {datetime.now().isoformat()}
type: agendino-transcript
---

# {title}

{tasks_section}{processed_summary}
"""
            
            filepath.write_text(content, encoding='utf-8')
            
            # Auto-commit if script is configured
            if self.auto_commit_script and os.path.exists(self.auto_commit_script):
                try:
                    logging.info(f"Running auto-commit script: {self.auto_commit_script}")
                    result = subprocess.run(
                        ['/usr/bin/sudo', '-u', 'git', self.auto_commit_script],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    logging.info(f"Auto-commit stdout: {result.stdout}")
                    logging.info(f"Auto-commit stderr: {result.stderr}")
                    logging.info(f"Auto-commit return code: {result.returncode}")
                except Exception as e:
                    logging.error(f"Auto-commit failed: {e}")
            
            return {
                "ok": True,
                "path": str(filepath),
                "message": f"Published to Obsidian: {filename}"
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
