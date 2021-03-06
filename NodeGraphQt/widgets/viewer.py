#!/usr/bin/python
from sys import platform

from PySide2 import QtGui, QtCore, QtWidgets

from NodeGraphQt.widgets.constants import (IN_PORT, OUT_PORT,
                                           PIPE_LAYOUT_CURVED,
                                           PIPE_LAYOUT_STRAIGHT,
                                           PIPE_STYLE_DASHED)
from NodeGraphQt.widgets.node_abstract import AbstractNodeItem
from NodeGraphQt.widgets.node_backdrop import BackdropNodeItem
from NodeGraphQt.widgets.pipe import Pipe
from NodeGraphQt.widgets.port import PortItem
from NodeGraphQt.widgets.scene import NodeScene
from NodeGraphQt.widgets.stylesheet import STYLE_QMENU
from NodeGraphQt.widgets.tab_search import TabSearchWidget

ZOOM_LIMIT = 12


class ContextMenu(object):

    def __init__(self, view, menu):
        self.__view = view
        self.__menu = menu

    @property
    def _menu_obj(self):
        return self.__menu

    def get_menu(self, name):
        ctx_menu = self.__view.context_menu()
        root_menu = ctx_menu._menu_obj
        for action in root_menu.actions():
            if action.text() != name:
                continue
            menu = action.menu()
            return ContextMenu(self.__view, menu)

    def add_action(self, action):
        action.setShortcutVisibleInContextMenu(True)
        self.__menu.addAction(action)

    def add_menu(self, name):
        menu = QtWidgets.QMenu(None, title=name)
        menu.setStyleSheet(STYLE_QMENU)
        self.__menu.addMenu(menu)
        return ContextMenu(self.__view, menu)

    def add_command(self, name, func=None, shortcut=None):
        action = QtWidgets.QAction(name, self.__view)
        action.setShortcutVisibleInContextMenu(True)
        if shortcut:
            action.setShortcut(shortcut)
        if func:
            action.triggered.connect(func)
        self.__menu.addAction(action, shortcut=shortcut)

    def add_separator(self):
        self.__menu.addSeparator()


class NodeViewer(QtWidgets.QGraphicsView):

    moved_nodes = QtCore.Signal(dict)
    search_triggered = QtCore.Signal(str, tuple)
    connection_changed = QtCore.Signal(list, list)

    def __init__(self, parent=None):
        super(NodeViewer, self).__init__(parent)
        scene_area = 8000.0
        scene_pos = (scene_area / 2) * -1
        self.setScene(NodeScene(self))
        self.setSceneRect(scene_pos, scene_pos, scene_area, scene_area)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self._zoom = 0
        self._pipe_layout = PIPE_LAYOUT_CURVED
        self._live_pipe = None
        self._detached_port = None
        self._start_port = None
        self._origin_pos = None
        self._previous_pos = QtCore.QPoint(self.width(), self.height())
        self._prev_selection = []
        self._node_positions = {}
        self._rubber_band = QtWidgets.QRubberBand(
            QtWidgets.QRubberBand.Rectangle, self
        )
        self._undo_stack = QtWidgets.QUndoStack(self)
        self._context_menu = QtWidgets.QMenu('nodes', self)
        self._context_menu.setStyleSheet(STYLE_QMENU)
        self._search_widget = TabSearchWidget(self)
        self._search_widget.search_submitted.connect(self._on_search_submitted)

        # workaround fix on OSX shortcuts from the non-native menu actions
        # don't seem to trigger so we create a dummy menu bar.
        if platform == 'darwin':
            menu_bar = QtWidgets.QMenuBar(self)
            menu_bar.setNativeMenuBar(False)
            menu_bar.resize(0, 0)
            menu_bar.addMenu(self._context_menu)

        self.acyclic = True
        self.LMB_state = False
        self.RMB_state = False
        self.MMB_state = False

    def __str__(self):
        return '{}.{}()'.format(
            self.__module__, self.__class__.__name__)

    def __repr__(self):
        return '{}.{}()'.format(
            self.__module__, self.__class__.__name__)

    # --- private methods ---

    def _set_viewer_zoom(self, value):
        max_zoom = ZOOM_LIMIT
        min_zoom = max_zoom * -1
        if value > 0.0:
            if self._zoom <= max_zoom:
                self._zoom += 1
        else:
            if self._zoom >= min_zoom:
                self._zoom -= 1
        if self._zoom == 0:
            self.fitInView()
            return
        if self._zoom >= max_zoom:
            return
        if self._zoom <= min_zoom:
            return
        scale = 1.0 + value
        self.scale(scale, scale)

    def _set_viewer_pan(self, pos_x, pos_y):
        scroll_x = self.horizontalScrollBar()
        scroll_y = self.verticalScrollBar()
        scroll_x.setValue(scroll_x.value() - pos_x)
        scroll_y.setValue(scroll_y.value() - pos_y)

    def _combined_rect(self, nodes):
        group = self.scene().createItemGroup(nodes)
        rect = group.boundingRect()
        self.scene().destroyItemGroup(group)
        return rect

    def _items_near(self, pos, item_type=None, width=20, height=20):
        x, y = pos.x() - width, pos.y() - height
        rect = QtCore.QRect(x, y, width, height)
        items = []
        for item in self.scene().items(rect):
            if not item_type or isinstance(item, item_type):
                items.append(item)
        return items

    def _on_search_submitted(self, node_type):
        pos = self.mapToScene(self._previous_pos)
        self.search_triggered.emit(node_type, (pos.x(), pos.y()))

    # --- re-implemented methods ---

    def resizeEvent(self, event):
        super(NodeViewer, self).resizeEvent(event)

    def contextMenuEvent(self, event):
        self.RMB_state = False
        self._context_menu.exec_(event.globalPos())

    def mousePressEvent(self, event):
        alt_modifier = event.modifiers() == QtCore.Qt.AltModifier
        shift_modifier = event.modifiers() == QtCore.Qt.ShiftModifier
        if event.button() == QtCore.Qt.LeftButton:
            self.LMB_state = True
        elif event.button() == QtCore.Qt.RightButton:
            self.RMB_state = True
        elif event.button() == QtCore.Qt.MiddleButton:
            self.MMB_state = True
        self._origin_pos = event.pos()
        self._previous_pos = event.pos()
        self._prev_selection = self.selected_nodes()

        # close tab search
        if self._search_widget.isVisible():
            self.tab_search_toggle()

        if alt_modifier:
            return

        items = self._items_near(self.mapToScene(event.pos()), None, 20, 20)
        nodes = [i for i in items if isinstance(i, AbstractNodeItem)]

        # toggle extend node selection.
        if shift_modifier:
            for node in nodes:
                node.selected = not node.selected

        # update the recorded node positions.
        self._node_positions.update({n: n.pos for n in self.selected_nodes()})

        # show selection selection marquee
        if self.LMB_state and not items:
            rect = QtCore.QRect(self._previous_pos, QtCore.QSize())
            rect = rect.normalized()
            map_rect = self.mapToScene(rect).boundingRect()
            self.scene().update(map_rect)
            self._rubber_band.setGeometry(rect)
            self._rubber_band.show()

        if not shift_modifier:
            super(NodeViewer, self).mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.LMB_state = False
        elif event.button() == QtCore.Qt.RightButton:
            self.RMB_state = False
        elif event.button() == QtCore.Qt.MiddleButton:
            self.MMB_state = False

        # hide selection marquee
        if self._rubber_band.isVisible():
            rect = self._rubber_band.rect()
            map_rect = self.mapToScene(rect).boundingRect()
            self._rubber_band.hide()
            self.scene().update(map_rect)

        # find position changed nodes and emit signal.
        moved_nodes = {
            n: pos for n, pos in self._node_positions.items() if n.pos != pos}
        if moved_nodes:
            self.moved_nodes.emit(moved_nodes)

        # reset recorded positions.
        self._node_positions = {}

        super(NodeViewer, self).mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        alt_modifier = event.modifiers() == QtCore.Qt.AltModifier
        shift_modifier = event.modifiers() == QtCore.Qt.ShiftModifier
        if self.MMB_state or (self.LMB_state and alt_modifier):
            pos_x = (event.x() - self._previous_pos.x())
            pos_y = (event.y() - self._previous_pos.y())
            self._set_viewer_pan(pos_x, pos_y)
        elif self.RMB_state:
            pos_x = (event.x() - self._previous_pos.x())
            zoom = 0.1 if pos_x > 0 else -0.1
            self._set_viewer_zoom(zoom)

        if self.LMB_state and self._rubber_band.isVisible():
            rect = QtCore.QRect(self._origin_pos, event.pos()).normalized()
            map_rect = self.mapToScene(rect).boundingRect()
            path = QtGui.QPainterPath()
            path.addRect(map_rect)
            self._rubber_band.setGeometry(rect)
            self.scene().setSelectionArea(path, QtCore.Qt.IntersectsItemShape)
            self.scene().update(map_rect)

            if shift_modifier and self._prev_selection:
                for node in self._prev_selection:
                    if node not in self.selected_nodes():
                        node.selected = True

        self._previous_pos = event.pos()
        super(NodeViewer, self).mouseMoveEvent(event)

    def wheelEvent(self, event):
        adjust = (event.delta() / 120) * 0.1
        self._set_viewer_zoom(adjust)

    def fitInView(self):
        unity = self.transform().mapRect(QtCore.QRectF(0, 0, 1, 1))
        self.scale(1 / unity.width(), 1 / unity.height())
        self._zoom = 0

    # def dropEvent(self, event):
    #     if event.mimeData().hasFormat('component/name'):
    #         drop_str = str(event.mimeData().data('component/name'))
    #         drop_pos = event.pos()

    # def dragEnterEvent(self, event):
    #     if event.mimeData().hasFormat('component/name'):
    #         event.accept()

    # def dragMoveEvent(self, event):
    #     if event.mimeData().hasFormat('component/name'):
    #         event.accept()

    # --- viewer methods ---

    def start_live_connection(self, selected_port):
        """
        create new pipe for the connection.
        """
        if not selected_port:
            return
        self._start_port = selected_port
        self._live_pipe = Pipe()
        self._live_pipe.activate()
        self._live_pipe.style = PIPE_STYLE_DASHED
        if self._start_port.type == IN_PORT:
            self._live_pipe.input_port = self._start_port
        elif self._start_port == OUT_PORT:
            self._live_pipe.output_port = self._start_port
        self.scene().addItem(self._live_pipe)

    def end_live_connection(self):
        """
        delete live connection pipe and reset start port.
        """
        if self._live_pipe:
            self._live_pipe.delete()
            self._live_pipe = None
        self._start_port = None

    def establish_connection(self, start_port, end_port):
        """
        establish a new pipe connection.
        """
        pipe = Pipe()
        self.scene().addItem(pipe)
        pipe.set_connections(start_port, end_port)
        pipe.draw_path(pipe.input_port, pipe.output_port)

    def acyclic_check(self, start_port, end_port):
        """
        validate the connection doesn't loop itself and
        returns True if port connection is valid.
        """
        start_node = start_port.node
        check_nodes = [end_port.node]
        io_types = {IN_PORT: 'outputs', OUT_PORT: 'inputs'}
        while check_nodes:
            check_node = check_nodes.pop(0)
            for check_port in getattr(check_node, io_types[end_port.port_type]):
                if check_port.connected_ports:
                    for port in check_port.connected_ports:
                        if port.node != start_node:
                            check_nodes.append(port.node)
                        else:
                            return False
        return True

    def sceneMouseMoveEvent(self, event):
        """
        triggered mouse move event for the scene.
         - redraw the connection pipe.

        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent):
                The event handler from the QtWidgets.QGraphicsScene
        """
        if not self._live_pipe:
            return
        if not self._start_port:
            return
        pos = event.scenePos()
        self._live_pipe.draw_path(self._start_port, None, pos)

    def sceneMousePressEvent(self, event):
        """
        triggered mouse press event for the scene (takes priority over viewer).
         - detect selected pipe and start connection.
         - remap Shift and Ctrl modifier.

        Args:
            event (QtWidgets.QGraphicsScenePressEvent):
                The event handler from the QtWidgets.QGraphicsScene
        """
        ctrl_modifier = event.modifiers() == QtCore.Qt.ControlModifier
        alt_modifier = event.modifiers() == QtCore.Qt.AltModifier
        shift_modifier = event.modifiers() == QtCore.Qt.ShiftModifier
        if shift_modifier:
            event.setModifiers(QtCore.Qt.ControlModifier)
        elif ctrl_modifier:
            event.setModifiers(QtCore.Qt.ShiftModifier)

        if not alt_modifier:
            pos = event.scenePos()
            port_items = self._items_near(pos, PortItem, 5, 5)
            if port_items:
                port = port_items[0]
                if not port.multi_connection and port.connected_ports:
                    self._detached_port = port.connected_ports[0]
                self.start_live_connection(port)
                if not port.multi_connection:
                    [p.delete() for p in port.connected_pipes]
                return

            node_items = self._items_near(pos, AbstractNodeItem, 3, 3)
            if node_items:
                # record the node positions at selection time.
                for n in node_items:
                    self._node_positions[n] = n.pos

                if not isinstance(node_items[0], BackdropNodeItem):
                    return

            pipe_items = self._items_near(pos, Pipe, 3, 3)
            if pipe_items:
                pipe = pipe_items[0]
                attr = {IN_PORT: 'output_port', OUT_PORT: 'input_port'}
                from_port = pipe.port_from_pos(pos, True)
                to_port = getattr(pipe, attr[from_port.port_type])
                if not from_port.multi_connection and from_port.connected_ports:
                    self._detached_port = from_port.connected_ports[0]
                elif not to_port.multi_connection:
                    self._detached_port = to_port

                self.start_live_connection(from_port)
                self._live_pipe.draw_path(self._start_port, None, pos)
                pipe.delete()

    def sceneMouseReleaseEvent(self, event):
        """
        triggered mouse release event for the scene.
         - verify to make a the connection Pipe.
        
        Args:
            event (QtWidgets.QGraphicsSceneMouseEvent):
                The event handler from the QtWidgets.QGraphicsScene
        """
        if event.modifiers() == QtCore.Qt.ShiftModifier:
            event.setModifiers(QtCore.Qt.ControlModifier)

        if not self._live_pipe:
            return

        # find the end port.
        end_port = None
        for item in self.scene().items(event.scenePos()):
            if isinstance(item, PortItem):
                end_port = item
                break

        connected = []
        disconnected = []

        # if port disconnected from existing pipe.
        if end_port is None:
            if self._detached_port:
                disconnected.append((self._start_port, self._detached_port))
                self.connection_changed.emit(disconnected, connected)

            self._detached_port = None
            self.end_live_connection()
            return

        # restore connection check.
        restore_connection = any([
            # if same port type.
            end_port.port_type == self._start_port.port_type,
            # if connection to itself.
            end_port.node == self._start_port.node,
            # if end port is the start port.
            end_port == self._start_port,
            # if detached port is the end port.
            self._detached_port == end_port
        ])
        if restore_connection:
            if self._detached_port:
                to_port = self._detached_port or end_port
                self.establish_connection(self._start_port, to_port)
                self._detached_port = None
            self.end_live_connection()
            return

        # register as disconnected if not acyclic.
        if self.acyclic and not self.acyclic_check(self._start_port, end_port):
            if self._detached_port:
                disconnected.append((self._start_port, self._detached_port))

            self.connection_changed.emit(disconnected, connected)

            self._detached_port = None
            self.end_live_connection()
            return

        # make connection.
        if not end_port.multi_connection and end_port.connected_ports:
            dettached_end = end_port.connected_ports[0]
            disconnected.append((end_port, dettached_end))

        if self._detached_port:
            disconnected.append((self._start_port, self._detached_port))

        connected.append((self._start_port, end_port))

        self.connection_changed.emit(disconnected, connected)

        self._detached_port = None
        self.end_live_connection()

    def tab_search_set_nodes(self, nodes):
        self._search_widget.set_nodes(nodes)

    def tab_search_toggle(self):
        pos = self._previous_pos
        state = not self._search_widget.isVisible()
        if state:
            rect = self._search_widget.rect()
            new_pos = QtCore.QPoint(pos.x() - rect.width() / 2,
                                    pos.y() - rect.height() / 2)
            self._search_widget.move(new_pos)
            self._search_widget.setVisible(state)
            rect = self.mapToScene(rect).boundingRect()
            self.scene().update(rect)
        else:
            self._search_widget.setVisible(state)
            self.clearFocus()

    def context_menu(self):
        return ContextMenu(self, self._context_menu)

    def question_dialog(self, title, text):
        dlg = QtWidgets.QMessageBox.question(
            self, title, text,
            QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No)
        return dlg == QtWidgets.QMessageBox.Yes

    def message_dialog(self, text, title='node graph'):
        QtWidgets.QMessageBox.information(
            self, title, text, QtWidgets.QMessageBox.Ok)

    def all_pipes(self):
        pipes = []
        for item in self.scene().items():
            if isinstance(item, Pipe):
                pipes.append(item)
        return pipes

    def all_nodes(self):
        nodes = []
        for item in self.scene().items():
            if isinstance(item, AbstractNodeItem):
                nodes.append(item)
        return nodes

    def selected_nodes(self):
        nodes = []
        for item in self.scene().selectedItems():
            if isinstance(item, AbstractNodeItem):
                nodes.append(item)
        return nodes

    def add_node(self, node, pos=None):
        pos = pos or (self._previous_pos.x(), self._previous_pos.y())
        node.pre_init(self, pos)
        self.scene().addItem(node)
        node.post_init(self, pos)

    def remove_node(self, node):
        if isinstance(node, AbstractNodeItem):
            node.delete()

    def move_nodes(self, nodes, pos=None, offset=None):
        group = self.scene().createItemGroup(nodes)
        group_rect = group.boundingRect()
        if pos:
            x, y = pos
        else:
            pos = self.mapToScene(self._previous_pos)
            x = pos.x() - group_rect.center().x()
            y = pos.y() - group_rect.center().y()
        if offset:
            x += offset[0]
            y += offset[1]
        group.setPos(x, y)
        self.scene().destroyItemGroup(group)

    def get_pipes_from_nodes(self, nodes=None):
        nodes = nodes or self.selected_nodes()
        if not nodes:
            return
        pipes = []
        for node in nodes:
            n_inputs = node.inputs if hasattr(node, 'inputs') else []
            n_outputs = node.outputs if hasattr(node, 'outputs') else []

            for port in n_inputs:
                for pipe in port.connected_pipes:
                    connected_node = pipe.output_port.node
                    if connected_node in nodes:
                        pipes.append(pipe)
            for port in n_outputs:
                for pipe in port.connected_pipes:
                    connected_node = pipe.input_port.node
                    if connected_node in nodes:
                        pipes.append(pipe)
        return pipes

    def center_selection(self, nodes=None):
        if not nodes:
            if self.selected_nodes():
                nodes = self.selected_nodes()
            elif self.all_nodes():
                nodes = self.all_nodes()
        if len(nodes) == 1:
            self.centerOn(nodes[0])
        else:
            rect = self._combined_rect(nodes)
            self.centerOn(rect.center().x(), rect.center().y())

    def get_pipe_layout(self):
        return self._pipe_layout

    def set_pipe_layout(self, layout=''):
        layout_types = {
            'curved': PIPE_LAYOUT_CURVED,
            'straight': PIPE_LAYOUT_STRAIGHT
        }
        self._pipe_layout = layout_types.get(layout, 'curved')
        for pipe in self.all_pipes():
            pipe.draw_path(pipe.input_port, pipe.output_port)

    def get_zoom(self):
        return self._zoom

    def set_zoom(self, zoom=0):
        if zoom == 0:
            self.fitInView()
            return
        if zoom > 0 and zoom >= ZOOM_LIMIT:
            zoom = 12
        elif zoom <= (ZOOM_LIMIT * -1):
            zoom = -12
        zoom_factor = float(zoom) * 0.1
        self._set_viewer_zoom(zoom_factor)
