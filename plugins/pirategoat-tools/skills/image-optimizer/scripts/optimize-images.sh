#!/bin/bash
#
# optimize-images.sh - Optimize image assets with detailed reporting
#
# Usage:
#   optimize-images.sh [options] <target> [svgo_config_path] [temp_dir]
#
# Target can be:
#   - A directory (processes all images recursively)
#   - A single image file (png, jpg, jpeg, gif, svg)
#
# Options:
#   --cleanup    Clean up temp directory when done (default: keep temp dir)
#   --help       Show this help message
#
# Arguments:
#   target           - Directory or image file to optimize
#   svgo_config_path - Optional path to SVGO config file (use "" to skip)
#   temp_dir         - Optional temp directory (reuse to skip re-optimization)
#
# Workflow:
#   Step 1 (dry run):
#     optimize-images.sh ./assets "" /tmp/my-optimize
#     → Optimizes images, shows report, preserves temp dir for review
#
#   Step 2 (apply changes):
#     optimize-images.sh --cleanup ./assets "" /tmp/my-optimize
#     → Reuses optimized files, applies changes, cleans up temp dir
#
# Examples:
#   # Simple one-step usage (auto temp dir, always cleans up)
#   optimize-images.sh --cleanup ./assets
#
#   # Two-step workflow for review before applying
#   optimize-images.sh ./assets "" /tmp/img-optimize     # Step 1: Review
#   optimize-images.sh --cleanup ./assets "" /tmp/img-optimize  # Step 2: Apply
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse options
DO_CLEANUP=false
SHOW_HELP=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cleanup)
      DO_CLEANUP=true
      shift
      ;;
    --help|-h)
      SHOW_HELP=true
      shift
      ;;
    -*)
      echo -e "${RED}Error: Unknown option '$1'${NC}"
      echo "Use --help for usage information."
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

# Show help
if [ "$SHOW_HELP" = true ] || [ -z "$1" ]; then
  echo "Usage: optimize-images.sh [options] <target> [svgo_config_path] [temp_dir]"
  echo ""
  echo "Options:"
  echo "  --cleanup    Clean up temp directory when done (default: keep)"
  echo "  --help       Show this help message"
  echo ""
  echo "Arguments:"
  echo "  target           Directory or image file to optimize"
  echo "  svgo_config_path Optional SVGO config file (use \"\" to skip)"
  echo "  temp_dir         Optional temp directory (reuse to skip re-optimization)"
  echo ""
  echo "Two-step workflow:"
  echo "  1. optimize-images.sh ./assets \"\" /tmp/my-temp    # Review report"
  echo "  2. optimize-images.sh --cleanup ./assets \"\" /tmp/my-temp  # Apply + cleanup"
  exit 0
fi

TARGET="$1"
SVGO_CONFIG="${2:-}"
USER_TEMP_DIR="${3:-}"
IS_SINGLE_FILE=false

# Check if target is a file or directory
if [ -f "$TARGET" ]; then
  # Verify it's an image file (case-insensitive check)
  TARGET_LOWER=$(echo "$TARGET" | tr '[:upper:]' '[:lower:]')
  case "$TARGET_LOWER" in
    *.png|*.jpg|*.jpeg|*.gif|*.svg)
      IS_SINGLE_FILE=true
      TARGET_DIR="$(cd "$(dirname "$TARGET")" && pwd)"
      TARGET_FILE="$(basename "$TARGET")"
      TARGET="$TARGET_DIR/$TARGET_FILE"
      ;;
    *)
      echo -e "${RED}Error: '$TARGET' is not a supported image file (png, jpg, jpeg, gif, svg)${NC}"
      exit 1
      ;;
  esac
elif [ -d "$TARGET" ]; then
  # Convert to absolute path
  TARGET_DIR="$(cd "$TARGET" && pwd)"
else
  echo -e "${RED}Error: '$TARGET' does not exist${NC}"
  exit 1
fi

# Check for required tools
HAS_IMAGEOPTIM=false
HAS_SVGO=false
MISSING_TOOLS=()

if command -v imageoptim &>/dev/null; then
  HAS_IMAGEOPTIM=true
else
  MISSING_TOOLS+=("imageoptim")
  echo -e "${YELLOW}Warning: imageoptim not found. PNG/JPEG/GIF optimization will be skipped.${NC}"
  echo -e "${YELLOW}  Install: npm install -g imageoptim-cli${NC}"
  echo -e "${YELLOW}  Also requires ImageOptim.app: https://imageoptim.com/mac${NC}"
  echo ""
fi

if command -v svgo &>/dev/null; then
  HAS_SVGO=true
else
  MISSING_TOOLS+=("svgo")
  echo -e "${YELLOW}Warning: svgo not found. SVG optimization will be skipped.${NC}"
  echo -e "${YELLOW}  Install: npm install -g svgo${NC}"
  echo ""
fi

if [ "$HAS_IMAGEOPTIM" = false ] && [ "$HAS_SVGO" = false ]; then
  echo -e "${RED}Error: No optimization tools found.${NC}"
  echo ""
  echo "Install the required tools:"
  echo "  npm install -g imageoptim-cli   # For PNG, JPEG, GIF"
  echo "  npm install -g svgo             # For SVG"
  echo ""
  echo "Note: imageoptim-cli requires ImageOptim.app on macOS:"
  echo "  https://imageoptim.com/mac"
  exit 1
fi

# Create or use temporary directory
if [ -n "$USER_TEMP_DIR" ]; then
  TEMP_DIR="$USER_TEMP_DIR"
  mkdir -p "$TEMP_DIR"
else
  TEMP_DIR=$(mktemp -d)
  echo -e "${BLUE}Created temp directory: $TEMP_DIR${NC}"
fi

# Cleanup function
cleanup_temp() {
  if [ "$DO_CLEANUP" = true ] && [ -d "$TEMP_DIR" ]; then
    rm -rf "$TEMP_DIR"
    echo -e "${BLUE}Cleaned up temp directory.${NC}"
  fi
}

# Set trap for cleanup on exit (only if --cleanup flag is set)
trap cleanup_temp EXIT

# Build list of files to process
FILE_LIST="$TEMP_DIR/file_list.txt"

if [ "$IS_SINGLE_FILE" = true ]; then
  echo -e "${GREEN}Processing file: $TARGET${NC}"
  echo "$TARGET" > "$FILE_LIST"
else
  echo -e "${GREEN}Scanning for images in: $TARGET_DIR${NC}"
  find "$TARGET_DIR" -type f \( -iname "*.png" -o -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.gif" -o -iname "*.svg" \) 2>/dev/null | sort > "$FILE_LIST"
fi

FILE_COUNT=$(wc -l < "$FILE_LIST" | tr -d ' ')

if [ "$FILE_COUNT" -eq 0 ]; then
  echo -e "${YELLOW}No image files found${NC}"
  exit 0
fi

echo -e "${GREEN}Found $FILE_COUNT image file(s)${NC}"
echo ""

# Check if optimized files already exist in temp directory
TEMP_IMAGES="$TEMP_DIR/images"
SKIP_OPTIMIZATION=false

if [ -d "$TEMP_IMAGES" ]; then
  existing_count=$(find "$TEMP_IMAGES" -type f \( -iname "*.png" -o -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.gif" -o -iname "*.svg" \) 2>/dev/null | wc -l | tr -d ' ')
  if [ "$existing_count" -gt 0 ]; then
    echo -e "${BLUE}Found $existing_count pre-optimized file(s) in temp directory, skipping optimization...${NC}"
    echo ""
    SKIP_OPTIMIZATION=true
  fi
fi

if [ "$SKIP_OPTIMIZATION" = false ]; then
  # Copy files to temp directory preserving structure
  echo -e "${BLUE}Copying files to temporary location...${NC}"
  while IFS= read -r file; do
    rel_path="${file#$TARGET_DIR/}"
    dest_dir="$TEMP_IMAGES/$(dirname "$rel_path")"
    mkdir -p "$dest_dir"
    cp "$file" "$dest_dir/"
  done < "$FILE_LIST"
  echo ""

  # Run optimizations on temp copies
  if [ "$HAS_IMAGEOPTIM" = true ]; then
    # Check if there are raster images
    raster_count=$(find "$TEMP_IMAGES" -type f \( -iname "*.png" -o -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.gif" \) 2>/dev/null | wc -l | tr -d ' ')
    if [ "$raster_count" -gt 0 ]; then
      echo -e "${GREEN}Running ImageOptim on raster images...${NC}"
      imageoptim "$TEMP_IMAGES" 2>/dev/null || true
      echo ""
    fi
  fi

  if [ "$HAS_SVGO" = true ]; then
    # Check if there are SVG files
    svg_count=$(find "$TEMP_IMAGES" -type f -iname "*.svg" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$svg_count" -gt 0 ]; then
      echo -e "${GREEN}Running SVGO on SVG files...${NC}"
      if [ -n "$SVGO_CONFIG" ] && [ -f "$SVGO_CONFIG" ]; then
        svgo -rf "$TEMP_IMAGES" --config "$SVGO_CONFIG" 2>/dev/null || true
      else
        svgo -rf "$TEMP_IMAGES" 2>/dev/null || true
      fi
      echo ""
    fi
  fi
fi

# Generate comparison report
echo "═══════════════════════════════════════════════════════════════════════════"
echo "📊 IMAGE OPTIMIZATION REPORT"
echo "═══════════════════════════════════════════════════════════════════════════"
echo ""
printf "%-3s %-46s  %8s  %8s  %8s  %6s\n" "" "File" "Original" "Optimized" "Saved" "%"
echo "--- ----------------------------------------------  --------  ---------  --------  ------"

total_before=0
total_after=0
changed_count=0
unchanged_count=0
skipped_count=0

# Arrays to track files to update
declare -a files_to_update=()
declare -a temp_files=()

while IFS= read -r file; do
  rel_path="${file#$TARGET_DIR/}"
  temp_file="$TEMP_IMAGES/$rel_path"

  if [ ! -f "$temp_file" ]; then
    continue
  fi

  orig_size=$(stat -f "%z" "$file" 2>/dev/null || stat -c "%s" "$file" 2>/dev/null)
  new_size=$(stat -f "%z" "$temp_file" 2>/dev/null || stat -c "%s" "$temp_file" 2>/dev/null)

  saved=$((orig_size - new_size))
  if [ "$orig_size" -gt 0 ]; then
    pct=$(echo "scale=1; $saved * 100 / $orig_size" | bc)
  else
    pct="0.0"
  fi

  # Format sizes for display
  orig_kb=$(echo "scale=2; $orig_size / 1024" | bc)
  new_kb=$(echo "scale=2; $new_size / 1024" | bc)
  saved_kb=$(echo "scale=2; $saved / 1024" | bc)

  # Determine status
  if [ "$saved" -gt 0 ]; then
    icon="✅"
    changed_count=$((changed_count + 1))
    files_to_update+=("$file")
    temp_files+=("$temp_file")
  elif [ "$saved" -lt 0 ]; then
    icon="⚠️"
    skipped_count=$((skipped_count + 1))
    saved_kb="(+$(echo "scale=2; ($new_size - $orig_size) / 1024" | bc))"
    pct="n/a"
  else
    icon="⬜"
    unchanged_count=$((unchanged_count + 1))
  fi

  printf "%s %-46s  %6.2fKB  %7.2fKB  %8s  %5s%%\n" "$icon" "$rel_path" "$orig_kb" "$new_kb" "$saved_kb" "$pct"

  total_before=$((total_before + orig_size))
  # Only count savings for files that actually got smaller
  if [ "$saved" -gt 0 ]; then
    total_after=$((total_after + new_size))
  else
    total_after=$((total_after + orig_size))
  fi
done < "$FILE_LIST"

echo "--- ----------------------------------------------  --------  ---------  --------  ------"

# Calculate totals
total_saved=$((total_before - total_after))
if [ "$total_before" -gt 0 ]; then
  total_pct=$(echo "scale=1; $total_saved * 100 / $total_before" | bc)
else
  total_pct="0.0"
fi

total_before_kb=$(echo "scale=2; $total_before / 1024" | bc)
total_after_kb=$(echo "scale=2; $total_after / 1024" | bc)
total_saved_kb=$(echo "scale=2; $total_saved / 1024" | bc)

printf "   %-46s  %6.2fKB  %7.2fKB  %6.2fKB  %5.1f%%\n" "TOTAL" "$total_before_kb" "$total_after_kb" "$total_saved_kb" "$total_pct"
echo ""
echo "═══════════════════════════════════════════════════════════════════════════"
echo "📈 Summary: ✅ $changed_count optimized | ⬜ $unchanged_count unchanged | ⚠️  $skipped_count larger"
echo "═══════════════════════════════════════════════════════════════════════════"
echo ""

# Show temp directory info if not cleaning up
if [ "$DO_CLEANUP" = false ]; then
  echo -e "${BLUE}Temp directory: $TEMP_DIR${NC}"
  echo -e "${BLUE}To apply changes later: optimize-images.sh --cleanup \"$TARGET_DIR\" \"\" \"$TEMP_DIR\"${NC}"
  echo ""
fi

# Ask for confirmation if there are files to update
if [ "$changed_count" -eq 0 ]; then
  echo -e "${YELLOW}No files need updating. All images are already optimized.${NC}"
  exit 0
fi

echo -e "${BLUE}$changed_count file(s) can be optimized, saving ${total_saved_kb}KB total.${NC}"
echo ""
printf "Overwrite original files with optimized versions? [y/N] "
read -r response

case "$response" in
  [yY][eE][sS]|[yY])
    echo ""
    echo -e "${GREEN}Updating files...${NC}"

    for i in "${!files_to_update[@]}"; do
      orig_file="${files_to_update[$i]}"
      temp_file="${temp_files[$i]}"
      rel_path="${orig_file#$TARGET_DIR/}"

      cp "$temp_file" "$orig_file"
      echo "  ✅ $rel_path"
    done

    echo ""
    echo -e "${GREEN}Done! $changed_count file(s) updated.${NC}"
    ;;
  *)
    echo ""
    echo -e "${YELLOW}Cancelled. No files were modified.${NC}"
    if [ "$DO_CLEANUP" = false ]; then
      echo -e "${BLUE}Temp directory preserved at: $TEMP_DIR${NC}"
    fi
    ;;
esac