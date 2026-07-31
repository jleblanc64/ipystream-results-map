from IPython.display import HTML, display
from ipywidgets import widgets

html_radio_wrapping = """
<style>
.widget-radio-box label {
  white-space: normal !important;
  display: flex !important;
  align-items: center !important; /* Changed from flex-start to center */
  margin-bottom: 5px !important;
  min-height: fit-content !important;
  height: auto !important;
  flex-direction: row-reverse !important;
  justify-content: flex-end !important;
}

.widget-radio-box label input[type="radio"] {
  margin-right: 12px !important;
}
</style>
"""

def apply():
    out = widgets.Output()
    display(out)
    out.append_display_data(HTML(html_radio_wrapping))