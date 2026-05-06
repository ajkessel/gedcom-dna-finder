"""
gedcom_gui_appearance.py

AppearanceMixin — history/config, font, theme, keybindings, and menu methods
for DNAMatchFinderApp.
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox
import sys
from gedcom_strings import *  # noqa: F401,F403
from gedcom_theme import _detect_system_theme, Tooltip, THEME_NAMES, THEMES


class AppearanceMixin:
    """Mixin providing appearance, history, keybinding, and menu methods."""

    # ---------------------------------------------------------- History / config
    def _cache_dir(self):
        """Return the directory used for GEDCOM parse caches."""
        return self._config._path.parent / 'cache'

    def _load_history(self):
        """Load the recently opened GEDCOM file list."""
        return self._config.get_recent_files()

    def _save_history(self, history):
        """Persist the recently opened GEDCOM file list."""
        self._config.set_recent_files(history)

    def _add_to_history(self, filepath):
        """Add filepath to the recent-file list and update the combo box."""
        history = [filepath] + [p for p in self._recent_files if p != filepath]
        history = history[:self.MAX_RECENT]
        self._recent_files = history
        self.path_combo['values'] = history
        self._config.set_recent_files(history)

    def _clear_cache(self):
        """Confirm and remove cached GEDCOM parse files."""
        cache_dir = self._cache_dir()
        files = list(cache_dir.glob('*.json')) if cache_dir.exists() else []
        if not files:
            messagebox.showinfo(CACHE_EMPTY_TITLE, CACHE_EMPTY_MSG)
            return
        if messagebox.askyesno(
            CACHE_CLEAR_TITLE,
            CACHE_CLEAR_MSG.format(count=len(files)),
        ):
            deleted = self._model.clear_cache(cache_dir)
            messagebox.showinfo(
                CACHE_DONE_TITLE, CACHE_DONE_MSG.format(deleted=deleted))

    def _load_home_person(self, gedcom_path):
        """Load the saved home person ID for gedcom_path."""
        return self._config.get_home_person(gedcom_path)

    def _save_home_person(self, gedcom_path, indi_id):
        """Persist the home person ID for gedcom_path."""
        self._config.set_home_person(gedcom_path, indi_id)

    def _load_font_preference(self):
        """Load the saved UI font-size preference."""
        return self._config.get_font_preference(self._FONT_SIZES)

    def _save_font_preference(self, size_name):
        """Persist the UI font-size preference."""
        self._config.set_font_preference(size_name)

    def _apply_font_size(self, size_name):
        """Apply a named font-size preset to UI and monospace fonts."""
        sizes = self._FONT_SIZES[size_name]
        mono_sz = sizes['mono']
        ui_sz = sizes['ui']

        self._mono_font.configure(size=mono_sz)
        self._mono_font_bold.configure(size=mono_sz)

        for fname in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkSmallCaptionFont'):
            try:
                tkfont.nametofont(fname).configure(size=ui_sz)
            except tk.TclError:
                pass

        self._apply_styles()

        if hasattr(self, 'results'):
            self.root.after(0, self._refit_windows)

    def _refit_windows(self):
        """Grow open windows as needed to fit the current font metrics."""
        self.root.update_idletasks()
        req_w = self.root.winfo_reqwidth()
        cur_w = self.root.winfo_width()
        cur_h = self.root.winfo_height()
        self.root.minsize(max(req_w, 800), 500)
        if cur_w < req_w:
            self.root.geometry(f"{req_w}x{cur_h}")
        for win in self.root.winfo_children():
            if not isinstance(win, tk.Toplevel):
                continue
            try:
                rw = win.winfo_reqwidth()
                rh = win.winfo_reqheight()
                cw = win.winfo_width()
                ch = win.winfo_height()
                new_w = max(cw, rw)
                new_h = max(ch, rh)
                if new_w != cw or new_h != ch:
                    win.geometry(f"{new_w}x{new_h}")
            except tk.TclError:
                pass

    def _apply_styles(self):
        """Apply the current theme colours + font metrics to the ttk Style engine."""
        style = ttk.Style()
        pref = getattr(self, '_theme_pref', 'Default')
        resolved = _detect_system_theme() if pref == 'System' else pref
        t = self._THEMES.get(resolved)

        if t is None:
            try:
                style.theme_use(self._default_ttk_theme)
            except tk.TclError:
                pass
        else:
            try:
                style.theme_use(t['ttk'])
            except tk.TclError:
                pass
            bg, fg = t['bg'], t['fg']
            bbg, fbg = t['button_bg'], t['field_bg']
            sel_bg, sel_fg = t['select_bg'], t['select_fg']
            hbg, tr = t['heading_bg'], t['trough']

            style.configure('.', background=bg, foreground=fg)
            style.configure('TFrame', background=bg)
            style.configure('TLabelframe', background=bg, foreground=fg)
            style.configure('TLabelframe.Label', background=bg, foreground=fg)
            style.configure('TLabel', background=bg, foreground=fg)
            style.configure('TButton', background=bbg, foreground=fg)
            style.map('TButton',
                      background=[('active', sel_bg), ('pressed', sel_bg)],
                      foreground=[('active', sel_fg), ('pressed', sel_fg)])
            style.configure('TEntry', fieldbackground=fbg, foreground=fg,
                            selectbackground=sel_bg, selectforeground=sel_fg)
            style.configure('TCombobox', fieldbackground=fbg, foreground=fg,
                            selectbackground=sel_bg, selectforeground=sel_fg,
                            background=bbg, arrowcolor=fg)
            style.map('TCombobox',
                      fieldbackground=[('readonly', fbg)],
                      selectbackground=[('readonly', sel_bg)],
                      foreground=[('readonly', fg)])
            style.configure('TSpinbox', fieldbackground=fbg, foreground=fg,
                            background=bbg, arrowcolor=fg)
            style.configure('TCheckbutton', background=bg, foreground=fg)
            style.map('TCheckbutton', background=[('active', bg)])
            style.configure('TRadiobutton', background=bg, foreground=fg)
            style.map('TRadiobutton', background=[('active', bg)])
            style.configure('TScrollbar', background=bbg, troughcolor=tr,
                            arrowcolor=fg, bordercolor=bg,
                            darkcolor=bbg, lightcolor=bbg)
            style.configure('TPanedwindow', background=bg)
            style.configure('Treeview', background=fbg, foreground=fg,
                            fieldbackground=fbg)
            style.configure('Treeview.Heading', background=hbg, foreground=fg)
            style.map('Treeview',
                      background=[('selected', sel_bg)],
                      foreground=[('selected', sel_fg)])

        row_h = tkfont.nametofont('TkDefaultFont').metrics('linespace') + 6
        style.configure('Treeview', font='TkDefaultFont', rowheight=row_h)
        style.configure('Treeview.Heading', font='TkDefaultFont')

    def _apply_theme(self, theme_name):
        """Apply a named color theme to the application."""
        self._theme_pref = theme_name
        resolved = _detect_system_theme() if theme_name == 'System' else theme_name
        t = self._THEMES.get(resolved)
        self._apply_styles()
        self._recolor_all(t)
        if hasattr(self, 'tree'):
            self.tree.tag_configure(
                'flagged_row',
                background=t['flag_bg'] if t else '#fff4cc',
            )
        if hasattr(self, 'results'):
            self.root.after(0, self._refit_windows)

    def _recolor_all(self, theme, start_widget=None):
        """Recolor every tk.Text widget and window background to match theme."""
        if theme is None:
            text_bg, text_fg = 'white', 'black'
            insert_col = 'black'
            sel_bg, sel_fg = '#0078d4', 'white'
            link_col = '#0066cc'
            root_bg = None
        else:
            text_bg, text_fg = theme['text_bg'], theme['text_fg']
            insert_col = theme['insert']
            sel_bg, sel_fg = theme['select_bg'], theme['select_fg']
            link_col = theme['link']
            root_bg = theme['bg']

        self._link_color = link_col

        def recolor(widget):
            try:
                if isinstance(widget, tk.Text):
                    widget.configure(
                        bg=text_bg, fg=text_fg,
                        insertbackground=insert_col,
                        selectbackground=sel_bg,
                        selectforeground=sel_fg,
                    )
                    if hasattr(widget, 'frame'):
                        widget.frame.configure(bg=text_bg)
                    for tag in widget.tag_names():
                        if tag.startswith('pers_'):
                            widget.tag_configure(tag, foreground=link_col)
                elif isinstance(widget, (tk.Tk, tk.Toplevel)) and root_bg:
                    widget.configure(bg=root_bg)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                recolor(child)

        recolor(start_widget if start_widget is not None else self.root)

    def _apply_theme_to_window(self, win):
        """Apply the active theme colours to a newly created Toplevel window."""
        pref = getattr(self, '_theme_pref', 'Default')
        resolved = _detect_system_theme() if pref == 'System' else pref
        t = self._THEMES.get(resolved)
        self._recolor_all(t, start_widget=win)

    def _load_theme_preference(self):
        """Load the saved color theme preference."""
        return self._config.get_theme_preference(self._THEME_NAMES)

    def _save_theme_preference(self, theme_name):
        """Persist the selected color theme preference."""
        self._config.set_theme_preference(theme_name)

    def _load_show_person_geometry(self):
        """Load the saved geometry for the person detail window."""
        return self._config.get_window_geometry('show_person_geometry')

    def _persist_show_person_geometry(self, win):
        """Persist the current person detail window geometry."""
        try:
            geo = win.geometry()
            self._show_person_geometry = geo
            self._config.set_window_geometry('show_person_geometry', geo)
        except Exception as e:  # pylint: disable=broad-except
            print(f"Error persisting show person geometry: {e}")

    def _set_home_person(self):
        """Save the selected person as the home person for the active GEDCOM."""
        if not self.individuals:
            messagebox.showwarning(ERR_NO_DATA_TITLE, ERR_NO_DATA_MSG)
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning(ERR_NO_SEL_TITLE, ERR_NO_SEL_MSG)
            return
        indi_id = sel[0]
        gedcom_path = self.gedcom_path.get().strip()
        if not gedcom_path:
            return
        self._home_person_id = indi_id
        self._save_home_person(gedcom_path, indi_id)
        name = self.individuals[indi_id]['name'] or indi_id
        self.status_text.set(STATUS_HOME_SET.format(name=name))

    # ---------------------------------------------------------- Keybindings
    def _setup_keybindings(self):
        """Register keyboard shortcuts and focus traversal for the main window."""
        def bind(seq, cmd):
            self.root.bind(seq, lambda _: cmd() or 'break')

        bind('<Control-f>', self._kb_focus_search)
        bind('<Control-i>', self._kb_focus_filter)
        bind('<Control-d>', lambda: self.show_flagged_only.set(
            not self.show_flagged_only.get()))
        bind('<Control-u>', lambda: self.fuzzy_search.set(
            not self.fuzzy_search.get()))
        bind('<Control-p>', self._find_path)
        bind('<Control-t>', self._view_tags)
        bind('<Control-o>', self._browse)
        bind('<Control-h>', self._set_home_person)
        bind('<Control-s>', self._show_person)
        bind('<Control-n>', self._find_matches)
        bind('<Control-l>', self._clear_results)
        bind('<Escape>', self._clear_results)
        # Ctrl-C: only invoke _copy_results when a Text widget isn't focused
        # (Text widgets capture Ctrl-C themselves to copy selected text).
        self.root.bind('<Control-c>', self._kb_copy)

        # Explicit tab chain:
        # tree → results → top_n → max_depth → set_home → show_person → find_matches
        self.results.configure(takefocus=True)
        tab_chain = [
            self.tree, self.results,
            self.top_n_spin, self.max_depth_spin,
            self.set_home_btn, self.show_person_btn, self.find_matches_btn,
        ]
        for i, w in enumerate(tab_chain):
            nxt = tab_chain[(i + 1) % len(tab_chain)]
            prv = tab_chain[(i - 1) % len(tab_chain)]
            w.bind('<Tab>', lambda _, nw=nxt: nw.focus_set() or 'break')
            w.bind('<Shift-Tab>', lambda _, pw=prv: pw.focus_set() or 'break')

        self.root.bind('<Alt-m>', lambda _: self._open_app_menu() or 'break')
        self.root.bind('<Alt-M>', lambda _: self._open_app_menu() or 'break')

        r = self.results
        r.bind('<Up>', lambda _: r.yview_scroll(-1, 'units') or 'break')
        r.bind('<Down>', lambda _: r.yview_scroll(1, 'units') or 'break')
        r.bind('<Prior>', lambda _: r.yview_scroll(-1, 'pages') or 'break')
        r.bind('<Next>', lambda _: r.yview_scroll(1, 'pages') or 'break')
        r.bind('<Home>', lambda _: r.yview_moveto(0) or 'break')
        r.bind('<End>', lambda _: r.yview_moveto(1) or 'break')

    def _open_app_menu(self):
        """Post the application menu at the top-left of the root window."""
        self.root.update_idletasks()
        x = self.root.winfo_rootx()
        y = self.root.winfo_rooty()
        self._app_menu.tk_popup(x, y)

    def _kb_focus_search(self):
        """Focus and select the main search field."""
        self.search_entry.focus_set()
        self.search_entry.select_range(0, 'end')

    def _kb_focus_filter(self):
        """Focus and select the raw GEDCOM filter field."""
        self.filter_entry.focus_set()
        self.filter_entry.select_range(0, 'end')

    def _kb_focus_list(self):
        """Focus the people list and select the first row when needed."""
        self.tree.focus_set()
        if not self.tree.focus():
            children = self.tree.get_children()
            if children:
                self.tree.focus(children[0])
                self.tree.selection_set(children[0])

    def _tree_jump(self, end, tree=None):
        """Move selection to the first or last row of a tree widget."""
        t = tree or self.tree
        children = t.get_children()
        if not children:
            return
        item = children[0] if end == 'first' else children[-1]
        t.focus_set()
        t.focus(item)
        t.selection_set(item)
        t.see(item)

    def _tree_type_ahead(self, event, tree=None):
        """Select the first tree row whose name starts with the typed character."""
        char = event.char
        if not char or not char.isalnum():
            return
        t = tree or self.tree
        char_lower = char.lower()
        children = t.get_children()
        if not children:
            return
        for item in children:
            name = t.set(item, 'name')
            if name.lower().startswith(char_lower):
                t.focus_set()
                t.focus(item)
                t.selection_set(item)
                t.see(item)
                return 'break'

    def _kb_copy(self, *_):
        """Handle Ctrl-C by copying results unless a Text widget has focus."""
        if isinstance(self.root.focus_get(), tk.Text):
            return  # let the text widget handle its own copy
        self._copy_results()
        return 'break'

    # ---------------------------------------------------------- Menu
    def _setup_menu(self):
        """Build the application menu and connect menu commands."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        self._menubar = menubar

        app_menu = tk.Menu(menubar, tearoff=0)
        self._app_menu = app_menu
        menubar.add_cascade(label=MENU_MENU, underline=0, menu=app_menu)
        app_menu.add_command(label=MENU_PREFERENCES, underline=0,
                             command=self._show_preferences)
        app_menu.add_command(label=MENU_CLEAR_CACHE, underline=0,
                             command=self._clear_cache)
        app_menu.add_separator()
        app_menu.add_command(label=MENU_HOW_TO_USE, underline=0,
                             command=self._show_how_to_use)
        app_menu.add_command(label=MENU_KEYBOARD_SHORTCUTS, underline=0,
                             command=self._show_keyboard_shortcuts)
        app_menu.add_command(label=MENU_PRIVACY_POLICY, underline=1,
                             command=self._show_privacy_policy)
        app_menu.add_command(label=MENU_ABOUT, underline=0,
                             command=self._show_about)

        # macOS supplies Quit via Cmd+Q automatically; only add it explicitly elsewhere.
        if sys.platform != 'darwin':
            app_menu.add_separator()
            app_menu.add_command(label=MENU_QUIT, underline=0,
                                 command=self.root.quit)
        else:
            self.root.createcommand('::tk::mac::Quit', self.root.quit)

    # ---------------------------------------------------------- Window centering helper
    def _center_on_root(self, win, w=None, h=None):
        """Center a Toplevel window over the root window."""
        win.update_idletasks()
        self.root.update_idletasks()
        if w is None:
            w = win.winfo_reqwidth()
        if h is None:
            h = win.winfo_reqheight()
        px = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        py = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        win.geometry(f"{w}x{h}+{px}+{py}")
