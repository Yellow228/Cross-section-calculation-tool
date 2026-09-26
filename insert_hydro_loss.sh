#!/bin/bash
# We will insert HydroLossDialog before AboutDialog
sed -i -e '/class AboutDialog/r patch_hydro_loss.py' -e '/class AboutDialog/N' src/app/dialogs.py
