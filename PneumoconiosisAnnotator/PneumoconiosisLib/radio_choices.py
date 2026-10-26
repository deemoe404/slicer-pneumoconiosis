"""Native, independently exclusive radio choices for structured ratings."""
import qt


class RadioChoices:
    def __init__(self, parent, options, changed):
        self.group = qt.QButtonGroup(parent)
        self.group.setExclusive(True)
        self.buttons = {}
        for value, label in options:
            button = qt.QRadioButton(label, parent)
            self.group.addButton(button)
            button.connect("toggled(bool)", lambda checked: changed() if checked else None)
            self.buttons[value] = button
        self.set_value(options[0][0])

    @property
    def value(self):
        return next(value for value, button in self.buttons.items() if button.isChecked())

    def set_value(self, value):
        self.buttons[value].setChecked(True)
