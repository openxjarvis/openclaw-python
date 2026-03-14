#!/usr/bin/env python3
"""Test script for root directory initialization."""
import tempfile
from pathlib import Path
from openclaw.agents.ensure_root_dirs import ensure_root_directories

# Create a temporary directory for testing
with tempfile.TemporaryDirectory() as tmpdir:
    test_dir = Path(tmpdir) / "test_openclaw"
    
    print(f"Testing root directory initialization in: {test_dir}")
    print("=" * 80)
    
    # Run the initialization
    result = ensure_root_directories(test_dir)
    
    # Check what was created
    print("\nCreated directories:")
    for item in test_dir.iterdir():
        if item.is_dir():
            print(f"  📁 {item.name}/")
            for subitem in item.iterdir():
                print(f"     - {subitem.name}")
    
    print("\nResult:", result)
    
    # Verify files
    print("\n" + "=" * 80)
    print("Verification:")
    
    checks = [
        ("identity/device.json", test_dir / "identity" / "device.json"),
        ("identity/device-auth.json", test_dir / "identity" / "device-auth.json"),
        ("delivery-queue/.gitignore", test_dir / "delivery-queue" / ".gitignore"),
        ("completions/openclaw.bash", test_dir / "completions" / "openclaw.bash"),
        ("completions/openclaw.zsh", test_dir / "completions" / "openclaw.zsh"),
        ("completions/openclaw.fish", test_dir / "completions" / "openclaw.fish"),
        ("completions/openclaw.ps1", test_dir / "completions" / "openclaw.ps1"),
        ("canvas/index.html", test_dir / "canvas" / "index.html"),
        ("logs/gateway.log", test_dir / "logs" / "gateway.log"),
        ("logs/gateway.err.log", test_dir / "logs" / "gateway.err.log"),
        ("logs/config-audit.jsonl", test_dir / "logs" / "config-audit.jsonl"),
    ]
    
    all_ok = True
    for name, path in checks:
        if path.exists():
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name} - MISSING")
            all_ok = False
    
    print("\n" + "=" * 80)
    if all_ok:
        print("✅ All checks passed!")
    else:
        print("❌ Some files are missing")
