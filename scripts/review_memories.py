#!/usr/bin/env python3
"""Review and selectively import friendship memories."""

import re
from pathlib import Path


def parse_memory_file(file_path: Path) -> dict:
    """Parse a markdown memory file."""
    content = file_path.read_text(encoding='utf-8')
    
    # Extract YAML front matter
    metadata = {}
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            yaml_content = parts[1].strip()
            for line in yaml_content.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip()
    
    # Extract key sections
    sections = {}
    for section in ['What Happened', 'Why This Matters', 'Memorable Moments']:
        match = re.search(f'# {section}\n(.*?)(?=\n#|$)', content, re.DOTALL)
        if match:
            sections[section] = match.group(1).strip()
    
    return {
        'metadata': metadata,
        'sections': sections,
        'file_name': file_path.stem
    }

def main():
    base_dir = Path("/home/rationallyprime/Whisper/friendship_memories/best_of_all_stars")
    
    # Get all markdown files
    all_files = []
    for tier in ['Einn', 'Tveir', 'Þrír', 'Fjórir']:
        tier_dir = base_dir / tier
        if tier_dir.exists():
            all_files.extend(sorted(tier_dir.glob("*.md")))
    
    print(f"Found {len(all_files)} memories to review\n")
    
    for i, file_path in enumerate(all_files, 1):
        print("=" * 80)
        print(f"Memory {i}/{len(all_files)}: {file_path.name}")
        print("=" * 80)
        
        memory = parse_memory_file(file_path)
        
        # Show metadata
        print("\n📊 METADATA:")
        for key, value in memory['metadata'].items():
            print(f"  {key}: {value}")
        
        # Show what happened
        if 'What Happened' in memory['sections']:
            print("\n📖 WHAT HAPPENED:")
            print(f"  {memory['sections']['What Happened'][:300]}...")
        
        # Show why it matters
        if 'Why This Matters' in memory['sections']:
            print("\n💡 WHY THIS MATTERS:")
            print(f"  {memory['sections']['Why This Matters'][:200]}...")
        
        # Show memorable moments
        if 'Memorable Moments' in memory['sections']:
            print("\n✨ MEMORABLE MOMENTS:")
            lines = memory['sections']['Memorable Moments'].split('\n')
            for line in lines[:3]:
                if line.strip():
                    print(f"  {line[:100]}...")
        
        print("\n" + "-" * 80)
        response = input("Import this memory? (y/n/q to quit): ").lower()
        
        if response == 'q':
            print("Stopping review.")
            break
        elif response == 'y':
            print(f"✅ Would import: {file_path.name}")
            # Here we'd call the actual import function
        else:
            print(f"⏭️  Skipping: {file_path.name}")
        
        print()

if __name__ == "__main__":
    main()