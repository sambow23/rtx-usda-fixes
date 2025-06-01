#!/usr/bin/env python3
"""
USDA Asset Cleanup Tool

This script identifies and removes broken asset replacements from USDA files.
It handles cases where:
1. Assets have `references = None` but no valid replacement model
2. Assets reference non-existent files
3. Assets have broken texture or model paths

Usage:
    python usda_cleanup.py <usda_file> [--asset-dir <path>] [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple, Set


class USDACleanup:
    def __init__(self, asset_dir: str = None, dry_run: bool = False):
        self.asset_dir = Path(asset_dir) if asset_dir else None
        self.dry_run = dry_run
        self.removed_blocks = []
        self.issues_found = []

    def check_asset_exists(self, asset_path: str) -> bool:
        """Check if an asset file exists."""
        if not asset_path or asset_path == "@@":
            return False
        
        # Remove the @ symbols and any trailing @
        clean_path = asset_path.strip('@')
        
        if self.asset_dir:
            # Handle relative paths that start with ./
            if clean_path.startswith('./'):
                clean_path = clean_path[2:]  # Remove the './' prefix
            
            # If the clean_path starts with 'assets/' and our asset_dir already ends with 'assets',
            # remove the 'assets/' prefix to avoid duplication
            if clean_path.startswith('assets/') and str(self.asset_dir).endswith('assets'):
                clean_path = clean_path[7:]  # Remove 'assets/' prefix
            
            # Check relative to asset directory
            full_path = self.asset_dir / clean_path
        else:
            # Check relative to current directory
            full_path = Path(clean_path)
        
        return full_path.exists()

    def extract_asset_references(self, block_content: str) -> List[str]:
        """Extract all asset references from a block."""
        references = []
        
        # Find prepend references (model files) - can be anywhere in the block
        prepend_matches = re.findall(r'prepend references = @([^@]+)@', block_content)
        references.extend(prepend_matches)
        
        # Find texture references - look for any asset input
        texture_matches = re.findall(r'asset inputs:\w+(?:_texture|_map|diffuse_texture|normalmap_texture|metallic_texture|reflectionroughness_texture|emissive_mask_texture) = @([^@]+)@', block_content)
        references.extend(texture_matches)
        
        # Also look for any other asset references that might be present
        general_asset_matches = re.findall(r'asset \w+ = @([^@]+)@', block_content)
        references.extend(general_asset_matches)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_references = []
        for ref in references:
            if ref not in seen and ref != "":
                seen.add(ref)
                unique_references.append(ref)
        
        return unique_references

    def is_broken_replacement(self, block_content: str) -> Tuple[bool, List[str]]:
        """
        Determine if a replacement block is broken.
        
        Returns:
            Tuple of (is_broken, list_of_issues)
        """
        issues = []
        
        # Check if it has references = None (hiding original mesh)
        has_references_none = 'references = None' in block_content
        
        # Extract all asset references
        asset_refs = self.extract_asset_references(block_content)
        
        # Check if any referenced assets exist
        existing_assets = []
        missing_assets = []
        
        for asset_ref in asset_refs:
            if self.check_asset_exists(asset_ref):
                existing_assets.append(asset_ref)
            else:
                missing_assets.append(asset_ref)
        
        # Determine if broken
        is_broken = False
        
        # Case 1: Has references = None but no replacement assets at all
        if has_references_none and not asset_refs:
            is_broken = True
            issues.append("Has 'references = None' but no replacement assets defined")
        
        # Case 2: Has references = None and replacement assets, but ALL are missing
        elif has_references_none and asset_refs and not existing_assets:
            is_broken = True
            issues.append("Has 'references = None' but all replacement assets are missing")
            issues.append(f"Missing assets: {missing_assets}")
        
        # Case 3: Has replacement assets but ALL are missing (even without references = None)
        elif asset_refs and not existing_assets:
            is_broken = True
            issues.append(f"All referenced assets are missing: {missing_assets}")
        
        # Report missing assets even if not completely broken
        if missing_assets and existing_assets:
            issues.append(f"Some assets are missing: {missing_assets}")
            issues.append(f"Existing assets: {existing_assets}")
        
        return is_broken, issues

    def find_replacement_blocks(self, content: str) -> List[Tuple[int, int, str]]:
        """
        Find all replacement blocks in the USDA content.
        
        Returns:
            List of tuples: (start_line, end_line, block_content)
        """
        lines = content.split('\n')
        blocks = []
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Look for mesh blocks
            if re.match(r'over "mesh_[A-F0-9]+" \(', line):
                start_line = i
                brace_count = 0
                paren_count = 0
                block_lines = []
                in_parentheses = True  # We start inside the parentheses of the mesh declaration
                found_opening_brace = False
                
                # Count opening parentheses and braces to find the complete block
                for j in range(i, len(lines)):
                    current_line = lines[j]
                    block_lines.append(current_line)
                    
                    # Count parentheses and braces
                    paren_count += current_line.count('(') - current_line.count(')')
                    brace_count += current_line.count('{') - current_line.count('}')
                    
                    # Check for opening brace
                    if '{' in current_line:
                        found_opening_brace = True
                    
                    # Check if we've closed the initial parentheses
                    if in_parentheses and paren_count == 0:
                        in_parentheses = False
                    
                    # If we've closed all braces and we're not in parentheses and we found an opening brace, we've found the end
                    if j > i and brace_count == 0 and not in_parentheses and found_opening_brace:
                        end_line = j
                        block_content = '\n'.join(block_lines)
                        blocks.append((start_line, end_line, block_content))
                        i = j + 1
                        break
                else:
                    # If we reach here, we didn't find a proper closing
                    i += 1
            else:
                i += 1
        
        return blocks

    def process_file(self, file_path: str) -> bool:
        """
        Process a USDA file and remove broken replacements.
        
        Returns:
            True if any changes were made, False otherwise
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading file {file_path}: {e}")
            return False
        
        print(f"\nProcessing: {file_path}")
        
        # Find all replacement blocks
        blocks = self.find_replacement_blocks(content)
        print(f"Found {len(blocks)} replacement blocks")
        
        # Check each block for issues
        blocks_to_remove = []
        
        for start_line, end_line, block_content in blocks:
            is_broken, issues = self.is_broken_replacement(block_content)
            
            if issues:
                print(f"\nBlock at lines {start_line+1}-{end_line+1}:")
                for issue in issues:
                    print(f"  - {issue}")
                
                if is_broken:
                    print(f"  -> MARKED FOR REMOVAL")
                    blocks_to_remove.append((start_line, end_line, block_content))
                    self.removed_blocks.append({
                        'file': file_path,
                        'lines': f"{start_line+1}-{end_line+1}",
                        'issues': issues
                    })
        
        if not blocks_to_remove:
            print("No broken blocks found to remove.")
            return False
        
        if self.dry_run:
            print(f"\n[DRY RUN] Would remove {len(blocks_to_remove)} broken blocks")
            return False
        
        # Remove blocks (in reverse order to maintain line numbers)
        lines = content.split('\n')
        for start_line, end_line, _ in reversed(blocks_to_remove):
            del lines[start_line:end_line+1]
        
        # Write the cleaned content back
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            print(f"\nRemoved {len(blocks_to_remove)} broken blocks from {file_path}")
            return True
        except Exception as e:
            print(f"Error writing file {file_path}: {e}")
            return False

    def print_summary(self):
        """Print a summary of the cleanup operation."""
        print(f"\n{'='*60}")
        print("CLEANUP SUMMARY")
        print(f"{'='*60}")
        
        if not self.removed_blocks:
            print("No broken blocks were found or removed.")
            return
        
        print(f"Total broken blocks {'identified' if self.dry_run else 'removed'}: {len(self.removed_blocks)}")
        
        for block in self.removed_blocks:
            print(f"\nFile: {block['file']}")
            print(f"Lines: {block['lines']}")
            print("Issues:")
            for issue in block['issues']:
                print(f"  - {issue}")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up broken asset replacements in USDA files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python usda_cleanup.py scene.usda --dry-run
  python usda_cleanup.py scene.usda --asset-dir ./assets
  python usda_cleanup.py *.usda --asset-dir ./assets --dry-run
        """
    )
    
    parser.add_argument(
        'files',
        nargs='+',
        help='USDA file(s) to process'
    )
    
    parser.add_argument(
        '--asset-dir',
        type=str,
        help='Directory containing the asset files (for checking if referenced files exist)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be removed without actually modifying files'
    )
    
    args = parser.parse_args()
    
    # Validate asset directory
    if args.asset_dir and not os.path.isdir(args.asset_dir):
        print(f"Error: Asset directory '{args.asset_dir}' does not exist")
        sys.exit(1)
    
    # Initialize cleanup tool
    cleanup = USDACleanup(asset_dir=args.asset_dir, dry_run=args.dry_run)
    
    # Process files
    files_processed = 0
    files_modified = 0
    
    for file_pattern in args.files:
        # Handle glob patterns
        if '*' in file_pattern or '?' in file_pattern:
            import glob
            matching_files = glob.glob(file_pattern)
            if not matching_files:
                print(f"Warning: No files match pattern '{file_pattern}'")
                continue
        else:
            matching_files = [file_pattern]
        
        for file_path in matching_files:
            if not os.path.isfile(file_path):
                print(f"Warning: File '{file_path}' does not exist")
                continue
            
            files_processed += 1
            if cleanup.process_file(file_path):
                files_modified += 1
    
    # Print summary
    cleanup.print_summary()
    
    print(f"\nFiles processed: {files_processed}")
    if not args.dry_run:
        print(f"Files modified: {files_modified}")


if __name__ == "__main__":
    main() 