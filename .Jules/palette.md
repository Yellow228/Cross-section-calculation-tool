## 2024-05-24 - Dynamic Tooltips for Disabled States in Qt
**Learning:** In PySide6 desktop applications, disabled controls (like QPushButtons for "Clear" or "Reset") can be very confusing without contextual tooltips explaining *why* they are disabled. Static tooltips set once at creation often fail to convey the dynamic state of the application.
**Action:** Always pair `setEnabled(bool)` calls with `setToolTip(str)` updates to provide context for disabled interactive elements, improving clarity without adding extra visual noise.
