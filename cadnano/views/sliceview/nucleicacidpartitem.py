from ast import literal_eval

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem, QGraphicsSceneMouseEvent

from cadnano.proxies.cnenum import GridType, HandleType
from cadnano.fileio.lattice import HoneycombDnaPart, SquareDnaPart
from cadnano.controllers.nucleicacidpartitemcontroller import NucleicAcidPartItemController
from cadnano.gui.palette import getBrushObj, getNoBrush, getNoPen, getPenObj
from cadnano.part.nucleicacidpart import DEFAULT_RADIUS
from cadnano.views.abstractitems.abstractpartitem import QAbstractPartItem
from cadnano.views.resizehandles import ResizeHandleGroup
from cadnano.views.sliceview.sliceextras import ShortestPathHelper
from . import slicestyles as styles
from .griditem import GridItem
from .prexovermanager import PreXoverManager
from .virtualhelixitem import SliceVirtualHelixItem

_DEFAULT_WIDTH = styles.DEFAULT_PEN_WIDTH
_DEFAULT_ALPHA = styles.DEFAULT_ALPHA
_SELECTED_COLOR = styles.SELECTED_COLOR
_SELECTED_WIDTH = styles.SELECTED_PEN_WIDTH
_SELECTED_ALPHA = styles.SELECTED_ALPHA
_HANDLE_SIZE = 8


class SliceNucleicAcidPartItem(QAbstractPartItem):
    """Parent should be either a SliceRootItem, or an AssemblyItem.

    Invariant: keys in _empty_helix_hash = range(_nrows) x range(_ncols)
    where x is the cartesian product.

    Attributes:
        active_virtual_helix_item (cadnano.views.sliceview.virtualhelixitem.SliceVirtualHelixItem): Description
        resize_handle_group (ResizeHandleGroup): handles for dragging and resizing
        griditem (GridItem): Description
        outline (QGraphicsRectItem): Description
        prexover_manager (PreXoverManager): Description
        scale_factor (float): Description
    """
    _RADIUS = styles.SLICE_HELIX_RADIUS
    _RADIUS_TUPLE = (DEFAULT_RADIUS, _RADIUS)
    _BOUNDING_RECT_PADDING = 80

    def __init__(self, model_part_instance, viewroot, parent=None):
        """Summary

        Args:
            model_part_instance (TYPE): Description
            viewroot (TYPE): Description
            parent (None, optional): Description
        """
        super(SliceNucleicAcidPartItem, self).__init__(model_part_instance, viewroot, parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)

        self.shortest_path_start = None
        self.coordinates_to_vhid = dict()
        self._last_hovered_coord = None
        self._last_hovered_item = None
        self._highlighted_path = []
        self._highlighted_copypaste = []
        self.lock_hints = False
        self.copypaste_origin_offset = None
        self.spa_start_vhi = None
        self.last_mouse_position = None
        self._highlighted_grid_point = None

        self._getActiveTool = viewroot.manager.activeToolGetter
        m_p = self._model_part
        self._controller = NucleicAcidPartItemController(self, m_p)
        self.scale_factor = self._RADIUS / m_p.radius()
        self.inverse_scale_factor = m_p.radius() / self._RADIUS
        self.active_virtual_helix_item = None
        self.prexover_manager = PreXoverManager(self)
        self.hide()  # hide while until after attemptResize() to avoid flicker
        self._rect = QRectF(0., 0., 1000., 1000.)  # set this to a token value
        self.boundRectToModel()
        self.setPen(getNoPen())
        self.setRect(self._rect)
        self.setAcceptHoverEvents(True)

        self.shortest_path_add_mode = False

        # Cache of VHs that were active as of last call to activeSliceChanged
        # If None, all slices will be redrawn and the cache will be filled.
        # Connect destructor. This is for removing a part from scenes.

        # initialize the NucleicAcidPartItem with an empty set of old coords
        self.setZValue(styles.ZPARTITEM)
        self.outline = outline = QGraphicsRectItem(self)
        o_rect = self._configureOutline(outline)
        outline.setFlag(QGraphicsItem.GraphicsItemFlag.ItemStacksBehindParent)
        outline.setZValue(styles.ZDESELECTOR)
        model_color = m_p.getColor()
        self.outline.setPen(getPenObj(model_color, _DEFAULT_WIDTH))

        self.model_bounds_hint = QGraphicsRectItem(self)
        self.model_bounds_hint.setBrush(getBrushObj(model_color, alpha=12))
        self.model_bounds_hint.setPen(getNoPen())

        self.resize_handle_group = ResizeHandleGroup(o_rect, _HANDLE_SIZE, model_color, True,
                                                     HandleType.TOP |
                                                     HandleType.BOTTOM |
                                                     HandleType.LEFT |
                                                     HandleType.RIGHT |
                                                     HandleType.TOP_LEFT |
                                                     HandleType.TOP_RIGHT |
                                                     HandleType.BOTTOM_LEFT |
                                                     HandleType.BOTTOM_RIGHT,
                                                     self, show_coords=True)

        self.griditem = GridItem(self, self._model_props['grid_type'])
        self.griditem.setZValue(1)
        self.resize_handle_group.setZValue(2)

        self.x_axis_line = QGraphicsLineItem(0, 0, self._RADIUS, 0, self)
        self.x_axis_line.setPen(getPenObj('#cc0000', _DEFAULT_WIDTH))
        self.x_axis_line.setZValue(styles.ZAXIS)
        self.y_axis_line = QGraphicsLineItem(0, 0, 0, -self._RADIUS, self)
        self.y_axis_line.setPen(getPenObj('#007200', _DEFAULT_WIDTH))
        self.y_axis_line.setZValue(styles.ZAXIS)

        # select upon creation
        for part in m_p.document().children():
            if part is m_p:
                part.setSelected(True)
            else:
                part.setSelected(False)
        self.show()
    # end def

    ### SIGNALS ###

    ### SLOTS ###
    def partActiveVirtualHelixChangedSlot(self, part, id_num):
        """Summary

        Args:
            part (NucleicAcidPart): Description
            id_num (int): VirtualHelix ID number. See `NucleicAcidPart` for description and related methods.
        """
        vhi = self._virtual_helix_item_hash.get(id_num)
        self.setActiveVirtualHelixItem(vhi)
        self.setPreXoverItemsVisible(vhi)
    # end def

    def partActiveBaseInfoSlot(self, part, info):
        """Summary

        Args:
            part (TYPE): Description
            info (TYPE): Description

        Args:
            TYPE: Description
        """
        pxom = self.prexover_manager
        pxom.deactivateNeighbors()
        if info and info is not None:
            id_num, is_fwd, idx, _ = info
            pxom.activateNeighbors(id_num, is_fwd, idx)
    # end def

    def partPropertyChangedSlot(self, model_part, property_key, new_value):
        """Summary

        Args:
            model_part (Part): The model part
            property_key (TYPE): Description
            new_value (TYPE): Description

        Args:
            TYPE: Description
        """
        if self._model_part == model_part:
            self._model_props[property_key] = new_value
            if property_key == 'color':
                self.outline.setPen(getPenObj(new_value, _DEFAULT_WIDTH))
                for vhi in self._virtual_helix_item_hash.values():
                    vhi.updateAppearance()
                self.resize_handle_group.setPens(getPenObj(new_value, 0))
            elif property_key == 'is_visible':
                if new_value:
                    self.show()
                else:
                    self.hide()
            elif property_key == 'grid_type':
                self.griditem.setGridType(new_value)
    # end def

    def partRemovedSlot(self, sender):
        """docstring for partRemovedSlot

        Args:
            sender (obj): Model object that emitted the signal.
        """
        self.parentItem().removePartItem(self)

        scene = self.scene()

        scene.removeItem(self)

        self._model_part = None
        self._mod_circ = None

        self._controller.disconnectSignals()
        self._controller = None
        self.resize_handle_group.removeHandles()
        self.griditem = None
    # end def

    def partVirtualHelicesTranslatedSlot(self, sender, vh_set, left_overs, do_deselect):
        """
        left_overs are neighbors that need updating due to changes

        Args:
            sender (obj): Model object that emitted the signal.
            vh_set (TYPE): Description
            left_overs (TYPE): Description
            do_deselect (TYPE): Description
        """
        if do_deselect:
            tool = self._getActiveTool()
            if tool.methodPrefix() == "selectTool":
                if tool.isSelectionActive():
                    # tool.deselectItems()
                    tool.modelClear()

        # 1. move everything that moved
        for id_num in vh_set:
            vhi = self._virtual_helix_item_hash[id_num]
            vhi.updatePosition()
        # 2. now redraw what makes sense to be redrawn
        for id_num in vh_set:
            vhi = self._virtual_helix_item_hash[id_num]
            self._refreshVirtualHelixItemGizmos(id_num, vhi)
        for id_num in left_overs:
            vhi = self._virtual_helix_item_hash[id_num]
            self._refreshVirtualHelixItemGizmos(id_num, vhi)

        # 0. clear PreXovers:
        # self.prexover_manager.hideGroups()
        # if self.active_virtual_helix_item is not None:
        #     self.active_virtual_helix_item.deactivate()
        #     self.active_virtual_helix_item = None
        avhi = self.active_virtual_helix_item
        self.setPreXoverItemsVisible(avhi)
        self.enlargeRectToFit()
    # end def

    def _refreshVirtualHelixItemGizmos(self, id_num, vhi):
        """Update props and appearance of self & recent neighbors. Ultimately
        triggered by a partVirtualHelicesTranslatedSignal.

        Args:
            id_num (int): VirtualHelix ID number. See `NucleicAcidPart` for description and related methods.
            vhi (cadnano.views.sliceview.virtualhelixitem.SliceVirtualHelixItem): the item associated with id_num
        """
        neighbors = vhi.cnModel().getProperty('neighbors')
        neighbors = literal_eval(neighbors)
        vhi.beginAddWedgeGizmos()
        for nvh in neighbors:
            nvhi = self._virtual_helix_item_hash.get(nvh, False)
            if nvhi:
                vhi.setWedgeGizmo(nvh, nvhi)
        # end for
        vhi.endAddWedgeGizmos()
    # end def

    def partVirtualHelixPropertyChangedSlot(self, sender, id_num, virtual_helix, keys, values):
        """Summary

        Args:
            sender (obj): Model object that emitted the signal.
            id_num (int): VirtualHelix ID number. See `NucleicAcidPart` for description and related methods.
            keys (tuple): keys that changed
            values (tuple): new values for each key that changed

        Args:
            TYPE: Description
        """
        if self._model_part == sender:
            vh_i = self._virtual_helix_item_hash[id_num]
            vh_i.virtualHelixPropertyChangedSlot(keys, values)
    # end def

    def partVirtualHelixAddedSlot(self, sender, id_num, virtual_helix, neighbors):
        """Summary

        Args:
            sender (obj): Model object that emitted the signal.
            id_num (int): VirtualHelix ID number. See `NucleicAcidPart` for description and related methods.
            neighbors (TYPE): Description

        Args:
            TYPE: Description
        """
#        print('[NAPI] ADDED SLOT CALLED ON VH %s; called by %s' % (id_num, sender))
        vhi = SliceVirtualHelixItem(virtual_helix, self)
        self._virtual_helix_item_hash[id_num] = vhi
        self._refreshVirtualHelixItemGizmos(id_num, vhi)
        for neighbor_id in neighbors:
            nvhi = self._virtual_helix_item_hash.get(neighbor_id, False)
            if nvhi:
                self._refreshVirtualHelixItemGizmos(neighbor_id, nvhi)
        self.enlargeRectToFit()

        position = sender.locationQt(id_num=id_num,
                                     scale_factor=self.scale_factor)

        if self.griditem.grid_type is GridType.HONEYCOMB:
            coordinates = HoneycombDnaPart.positionModelToLatticeCoord(DEFAULT_RADIUS,
                                                                       position[0],
                                                                       position[1],
                                                                       scale_factor=self.scale_factor)
        else:
            coordinates = SquareDnaPart.positionModelToLatticeCoord(DEFAULT_RADIUS,
                                                                    position[0],
                                                                    position[1],
                                                                    scale_factor=self.scale_factor)

        assert id_num not in self.coordinates_to_vhid.values()
        if coordinates in self.coordinates_to_vhid.values():
            print('COORDINATES DUPLICATE %s in %s' % (coordinates, self.coordinates_to_vhid.values()))

        self.coordinates_to_vhid[coordinates] = id_num

        assert len(self.coordinates_to_vhid.keys()) == len(set(self.coordinates_to_vhid.keys()))
        assert len(self.coordinates_to_vhid.values()) == len(set(self.coordinates_to_vhid.values()))
    # end def

    def partVirtualHelixRemovingSlot(self, sender, id_num, virtual_helix, neighbors):
        """Summary

        Args:
            sender (obj): Model object that emitted the signal.
            id_num (int): VirtualHelix ID number. See `NucleicAcidPart` for description and related methods.
            neighbors (TYPE): Description

        Args:
            TYPE: Description
        """
        tm = self._viewroot.manager
        tm.resetTools()
        self.removeVirtualHelixItem(id_num)
        for neighbor_id in neighbors:
            nvhi = self._virtual_helix_item_hash[neighbor_id]
            self._refreshVirtualHelixItemGizmos(neighbor_id, nvhi)

        for coordinates, current_id in self.coordinates_to_vhid.items():
            if current_id == id_num:
                del self.coordinates_to_vhid[coordinates]
                break

        assert id_num not in self.coordinates_to_vhid.values()
        assert len(self.coordinates_to_vhid.keys()) == len(set(self.coordinates_to_vhid.keys()))
        assert len(self.coordinates_to_vhid.values()) == len(set(self.coordinates_to_vhid.values()))
    # end def

    def partSelectedChangedSlot(self, model_part, is_selected):
        """Set this Z to front, and return other Zs to default.

        Args:
            model_part (Part): The model part
            is_selected (TYPE): Description
        """
        if is_selected:
            # self._drag_handle.resetAppearance(_SELECTED_COLOR, _SELECTED_WIDTH, _SELECTED_ALPHA)
            self.setZValue(styles.ZPARTITEM + 1)
        else:
            # self._drag_handle.resetAppearance(self.modelColor(), _DEFAULT_WIDTH, _DEFAULT_ALPHA)
            self.setZValue(styles.ZPARTITEM)
    # end def

    def partVirtualHelicesSelectedSlot(self, sender, vh_set, is_adding):
        """is_adding (bool): adding (True) virtual helices to a selection
        or removing (False)

        Args:
            sender (obj): Model object that emitted the signal.
            vh_set (TYPE): Description
            is_adding (TYPE): Description
        """
        select_tool = self._viewroot.select_tool
        if is_adding:
            select_tool.selection_set.update(vh_set)
            select_tool.setPartItem(self)
            select_tool.getSelectionBoundingRect()
        else:
            select_tool.deselectSet(vh_set)
    # end def

    def partDocumentSettingChangedSlot(self, part, key, value):
        """Summary

        Args:
            part (TYPE): Description
            key (TYPE): Description
            value (TYPE): Description

        Args:
            TYPE: Description

        Raises:
            ValueError: Description
        """
        if key == 'grid':
            if value == 'lines and points':
                self.griditem.setDrawlines(True)
            elif value == 'points':
                self.griditem.setDrawlines(False)
            elif value == 'circles':
                pass  # self.griditem.setGridAppearance(False)
            else:
                raise ValueError("unknown grid styling")

    ### ACCESSORS ###
    def boundingRect(self):
        """Summary

        Args:
            TYPE: Description
        """
        return self._rect
    # end def

    def modelColor(self):
        """Summary

        Args:
            TYPE: Description
        """
        return self._model_props['color']
    # end def

    def window(self):
        """Summary

        Args:
            TYPE: Description
        """
        return self.parentItem().window()
    # end def

    def setActiveVirtualHelixItem(self, new_active_vhi):
        """Summary

        Args:
            new_active_vhi (TYPE): Description

        """
        current_vhi = self.active_virtual_helix_item
        if new_active_vhi != current_vhi:
            if current_vhi is not None:
                current_vhi.deactivate()
            if new_active_vhi is not None:
                new_active_vhi.activate()
            self.active_virtual_helix_item = new_active_vhi
    # end def

    def setPreXoverItemsVisible(self, virtual_helix_item):
        """
        self._pre_xover_items list references prexovers parented to other
        PathHelices such that only the activeHelix maintains the list of
        visible prexovers

        Args:
            virtual_helix_item (cadnano.views.sliceview.virtualhelixitem.SliceVirtualHelixItem): Description
        """
        vhi = virtual_helix_item
        pxom = self.prexover_manager
        if vhi is None:
            pxom.hideGroups()
            return

        part = self.part()
        info = part.active_base_info
        if info:
            id_num, is_fwd, idx, to_vh_id_num = info
            per_neighbor_hits, pairs = part.potentialCrossoverMap(id_num, idx)
            pxom.activateVirtualHelix(virtual_helix_item, idx,
                                      per_neighbor_hits, pairs)
    # end def

    def removeVirtualHelixItem(self, id_num):
        """Summary

        Args:
            id_num (int): VirtualHelix ID number. See `NucleicAcidPart` for description and related methods.

        Args:
            TYPE: Description
        """
        vhi = self._virtual_helix_item_hash[id_num]
        if vhi == self.active_virtual_helix_item:
            self.active_virtual_helix_item = None
        vhi.virtualHelixRemovedSlot()
        del self._virtual_helix_item_hash[id_num]

        # When any VH is removed, turn SPA mode off
        self.shortest_path_add_mode = False
        self.shortest_path_start = None
    # end def

    def reconfigureRect(self, top_left, bottom_right, finish=False, padding=80):
        """Reconfigures the rectangle that is the document.

        Args:
            top_left (tuple): A tuple corresponding to the x-y coordinates of
            top left corner of the document

            bottom_right (tuple): A tuple corresponding to the x-y coordinates
            of the bottom left corner of the document

        Returns:
            tuple: tuple of point tuples representing the top_left and
            bottom_right as reconfigured with padding
        """
        rect = self._rect
        ptTL = QPointF(*self.padTL(padding, *top_left)) if top_left else rect.topLeft()
        ptBR = QPointF(*self.padBR(padding, *bottom_right)) if bottom_right else rect.bottomRight()
        self._rect = QRectF(ptTL, ptBR)
        self.setRect(self._rect)
        self._configureOutline(self.outline)
        self.griditem.updateGrid()
        return self.outline.rect()
    # end def

    def padTL(self, padding, xTL, yTL):
        return xTL + padding, yTL + padding
    # end def

    def padBR(self, padding, xBR, yBR):
        return xBR - padding, yBR - padding
    # end def

    def enlargeRectToFit(self):
        """Enlarges Part Rectangle to fit the model bounds.

        This should be called when adding a SliceVirtualHelixItem.  This
        method enlarges the rectangle to ensure that it fits the design.
        This method needs to check the model size to do this, but also takes
        into account any expansions the user has made to the rectangle as to
        not shrink the rectangle after the user has expanded it.

        :rtype: None
        """
        padding = self._BOUNDING_RECT_PADDING

        model_left, model_top, model_right, model_bottom = self.getModelMinBounds()
        rect_left, rect_right, rect_bottom, rect_top = self.bounds()

        xTL = min(rect_left, model_left) - padding
        xBR = max(rect_right, model_right) + padding
        yTL = min(rect_top, model_top) - padding
        yBR = max(rect_bottom, model_bottom) + padding
        new_outline_rect = self.reconfigureRect(top_left=(xTL, yTL), bottom_right=(xBR, yBR))
        self.resize_handle_group.alignHandles(new_outline_rect)
        # self.grab_cornerTL.alignPos(*top_left)
        # self.grab_cornerBR.alignPos(*bottom_right)

    ### PRIVATE SUPPORT METHODS ###
    def _configureOutline(self, outline):
        """Adjusts `outline` size with default padding.

        Args:
            outline (TYPE): Description

        Returns:
            o_rect (QRect): `outline` rect adjusted by _BOUNDING_RECT_PADDING
        """
        _p = self._BOUNDING_RECT_PADDING
        o_rect = self.rect().adjusted(-_p, -_p, _p, _p)
        outline.setRect(o_rect)
        return o_rect
    # end def

    def boundRectToModel(self):
        """Update the size of the rectangle corresponding to the grid to
        the size of the model or a minimum size (whichever is greater).

        :rtype: None
        """
        xTL, yTL, xBR, yBR = self.getModelMinBounds()
        self._rect = QRectF(QPointF(xTL, yTL), QPointF(xBR, yBR))
    # end def

    def getModelMinBounds(self, handle_type=None):
        """Bounds in form of Qt scaled from model

        Args:
            Tuple (top_left, bottom_right)

        :rtype: Tuple where
        """
        xLL, yLL, xUR, yUR = self.part().boundDimensions(self.scale_factor)
        # return xLL, -yUR, xUR, -yLL
        r = self._RADIUS
        return xLL-r, -yUR-r, xUR+r, -yLL+r
    # end def

    def bounds(self):
        """x_low, x_high, y_low, y_high
        """
        rect = self._rect
        return (rect.left(), rect.right(), rect.bottom(), rect.top())

    ### PUBLIC SUPPORT METHODS ###
    def setLastHoveredItem(self, gridpoint_item):
        """Stores the last self-reported griditem to be hovered.

        Args:
            griditem (GridItem): the hoveree
        """
        self._last_hovered_item = gridpoint_item

    def setModifyState(self, bool_val):
        """Hides the mod_rect when modify state disabled.

        Args:
            bool_val (boolean): what the modifystate should be set to.
        """
        self._can_show_mod_circ = bool_val
        if bool_val is False:
            self._mod_circ.hide()
    # end def

    def showModelMinBoundsHint(self, handle_type, show=True):
        """Shows QGraphicsRectItem reflecting current model bounds.
        ResizeHandleGroup should toggle this when resizing.

        Args:
            status_str (str): Description to display in status bar.
        """
        m_b_h = self.model_bounds_hint
        if show:
            xTL, yTL, xBR, yBR = self.getModelMinBounds()
            m_b_h.setRect(QRectF(QPointF(xTL, yTL), QPointF(xBR, yBR)))
            m_b_h.show()
        else:
            m_b_h.hide()

    def updateStatusBar(self, status_str, timeout=0):
        """Shows status_str in the MainWindow's status bar.

        Args:
            status_str (str): Description to display in status bar.
        """
        self.window().statusBar().showMessage(status_str, timeout)
    # end def

    def zoomToFit(self):
        """Ask the view to zoom to fit.
        """
        thescene = self.scene()
        theview = thescene.views()[0]
        theview.zoomToFit()
    # end def

    ### EVENT HANDLERS ###
    def mousePressEvent(self, event):
        """Handler for user mouse press.

        Args:
            event (QGraphicsSceneMouseEvent): Contains item, scene, and screen
            coordinates of the the event, and previous event.

        Args:
            event (QMouseEvent): contains parameters that describe a mouse event.
        """
        if event.button() == Qt.MouseButton.RightButton:
            return
        part = self._model_part
        part.setSelected(True)
        if self.isMovable():
            return QGraphicsItem.mousePressEvent(self, event)
        tool = self._getActiveTool()
        if tool.FILTER_NAME not in part.document().filter_set:
            return
        tool_method_name = tool.methodPrefix() + "MousePress"
        if tool_method_name == 'createToolMousePress':
            return
        elif hasattr(self, tool_method_name):
            getattr(self, tool_method_name)(tool, event)
        else:
            event.setaccepted(False)
            QGraphicsItem.mousePressEvent(self, event)
    # end def

    def hoverMoveEvent(self, event):
        mapped_position = self.griditem.mapFromScene(event.scenePos())
        self.last_mouse_position = (mapped_position.x(), mapped_position.y())
        tool = self._getActiveTool()
        tool_method_name = tool.methodPrefix() + "HoverMove"
        if hasattr(self, tool_method_name):
            getattr(self, tool_method_name)(tool, event)
        else:
            event.setAccepted(False)
            QGraphicsItem.hoverMoveEvent(self, event)

    # def hoverLeaveEvent(self, event):
    #     pass
        # tool = self._getActiveTool()
        # tool.hideLineItem()

    def getModelPos(self, pos):
        """Y-axis is inverted in Qt +y === DOWN

        Args:
            pos (TYPE): Description
        """
        sf = self.scale_factor
        x, y = pos.x()/sf, -1.0*pos.y()/sf
        return x, y
    # end def

    def getVirtualHelixItem(self, id_num):
        """Summary

        Args:
            id_num (int): VirtualHelix ID number. See `NucleicAcidPart` for description and related methods.

        Returns:
            TYPE: Description
        """
        return self._virtual_helix_item_hash.get(id_num)
    # end def

    def keyPressEvent(self, event):
        is_alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        isInLatticeCoord = HoneycombDnaPart.isInLatticeCoord if self.griditem.grid_type is GridType.HONEYCOMB \
            else SquareDnaPart.isInLatticeCoord

        if event.key() == Qt.Key.Key_Escape:
#            print("Esc here")
            self._setShortestPathStart(None)
            self.removeAllCreateHints()

            if isInLatticeCoord(radius_tuple=self._RADIUS_TUPLE,
                                xy_tuple=self.last_mouse_position,
                                coordinate_tuple=self.getLastHoveredCoordinates(),
                                scale_factor=self.scale_factor):
                self.highlightOneGridPoint(self.getLastHoveredCoordinates())
            tool = self._getActiveTool()
            if tool.methodPrefix() == 'selectTool':
                self.removeAllCopyPasteHints()
                tool.clipboard = None
        elif is_alt and self.shortest_path_add_mode is True and isInLatticeCoord(radius_tuple=self._RADIUS_TUPLE,
                                                                                 xy_tuple=self.last_mouse_position,
                                                                                 coordinate_tuple=self.getLastHoveredCoordinates(),
                                                                                 scale_factor=self.scale_factor):
            self._previewSpa(self.last_mouse_position)
        elif is_alt and self.getLastHoveredCoordinates() and self.last_mouse_position:
            if isInLatticeCoord(radius_tuple=self._RADIUS_TUPLE,
                                xy_tuple=self.last_mouse_position,
                                coordinate_tuple=self.getLastHoveredCoordinates(),
                                scale_factor=self.scale_factor):
                coord = self.getLastHoveredCoordinates()
                self.highlightOneGridPoint(coord, styles.SPA_START_HINT_COLOR)
                self.griditem.highlightGridPoint(coord[0], coord[1], on=True)
    # end def

    def keyReleaseEvent(self, event):
        is_alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if not is_alt:
            self.removeAllCreateHints()
            isInLatticeCoord = HoneycombDnaPart.isInLatticeCoord if self.griditem.grid_type is GridType.HONEYCOMB \
                else SquareDnaPart.isInLatticeCoord
            if isInLatticeCoord(radius_tuple=self._RADIUS_TUPLE,
                                xy_tuple=self.last_mouse_position,
                                coordinate_tuple=self.getLastHoveredCoordinates(),
                                scale_factor=self.scale_factor):
                coord = self.getLastHoveredCoordinates()
                self.highlightOneGridPoint(coord)
                self.griditem.highlightGridPoint(coord[0], coord[1], on=True)
    # end def

    def createToolMousePress(self, tool, event, alt_event=None):
        """Creates individual or groups of VHs in Part on user input.
        Shift modifier enables multi-helix addition.

        Args:
            event (TYPE): Description
            alt_event (None, optional): Description
        """
        mapped_position = self.griditem.mapFromScene(event.scenePos())
        position = (mapped_position.x(), mapped_position.y())

        # 1. get point in model coordinates:
        part = self._model_part
        if alt_event is None:
            pt = tool.eventToPosition(self, event)
        else:
            pt = alt_event.pos()

        if pt is None:
            tool.deactivate()
            return QGraphicsItem.mousePressEvent(self, event)

        part_pt_tuple = self.getModelPos(pt)
        modifiers = event.modifiers()

        is_spa_mode = modifiers == Qt.KeyboardModifier.AltModifier
        last_added_spa_vhi_id = self._handleShortestPathMousePress(tool=tool,
                                                                   position=position,
                                                                   is_spa_mode=is_spa_mode)
        if last_added_spa_vhi_id is not None:
            return

        row, column = self.getLastHoveredCoordinates()
        parity = self._getCoordinateParity(row, column)

        part.createVirtualHelix(x=part_pt_tuple[0],
                                y=part_pt_tuple[1],
                                parity=parity)
        id_num = part.getVirtualHelixAtPoint(part_pt_tuple)
        vhi = self._virtual_helix_item_hash[id_num]
        tool.setVirtualHelixItem(vhi)
        tool.startCreation()

        if is_spa_mode:
            self._highlightSpaVH(id_num)
    # end def

    def _getModelXYforCoord(self, row, column):
        radius = DEFAULT_RADIUS
        if self.griditem.grid_type is GridType.HONEYCOMB:
            return HoneycombDnaPart.latticeCoordToQtXY(radius, row, column)
        elif self.griditem.grid_type is GridType.SQUARE:
            return SquareDnaPart.latticeCoordToQtXY(radius, row, column)
        else:
            return None
    # end def

    def _getCoordinateParity(self, row, column):
        if self.griditem.grid_type is GridType.HONEYCOMB:
            return 0 if HoneycombDnaPart.isEvenParity(row=row, column=column) else 1
        elif self.griditem.grid_type is GridType.SQUARE:
            return 0 if SquareDnaPart.isEvenParity(row=row, column=column) else 1
        else:
            return None
    # end def

    def _handleShortestPathMousePress(self, tool, position, is_spa_mode):
        """
        Handles logic for determining if SPA mode should be activated or
        continued.

        Args:
            tool ():
            position (tuple):  the xy coordinates of the mouse press
            is_spa_mode (bool):  whether or not this event is a SPA event

        Returns:
            True if nothing needs to be done by the caller (i.e. this method
            and its callees added VHs as necessary, False otherwise
        """
        if is_spa_mode:
            # Complete the path
            if self.shortest_path_start is not None:
                last_vhi_id = self.createToolShortestPath(tool=tool, start=self.shortest_path_start, end=position)
                if last_vhi_id is not None:
                    self._setShortestPathStart(position)
                    self._highlightSpaVH(last_vhi_id)
                    return last_vhi_id
            # Initialize SPA
            else:
                self._setShortestPathStart(position)
        else:
            self._setShortestPathStart(None)

    def _setShortestPathStart(self, position):
        # TODO[NF]:  Docstring
        if position is not None:
            self.shortest_path_add_mode = True
            self.shortest_path_start = position
        else:
            self.shortest_path_add_mode = False
            self.shortest_path_start = None
            self._highlightSpaVH(None)

    def _highlightSpaVH(self, vh_id):
        # TODO[NF]:  Docstring
        if self.spa_start_vhi:
            self.spa_start_vhi.setBrush(getNoBrush())

        if vh_id is None:
            self.spa_start_vhi = None
        else:
            self.spa_start_vhi = self._virtual_helix_item_hash[vh_id]
            self.spa_start_vhi.setBrush(getBrushObj(styles.SPA_START_HINT_COLOR, alpha=32))
    # end def

    def createToolShortestPath(self, tool, start, end):
        """
        Handle the creation of VHIs for SPA mode.

        Args:
            tool ():
            start (tuple):  the x-y coordinates of the start point
            end (tuple):  the x-y coordinates of the end point

        Returns:
            The ID of the last VHI created
        """
        path = ShortestPathHelper.shortestPathXY(start=start,
                                                 end=end,
                                                 vh_set=self.coordinates_to_vhid.keys(),
                                                 grid_type=self.griditem.grid_type,
                                                 scale_factor=self.scale_factor,
                                                 part_radius=DEFAULT_RADIUS)

        # Abort and exit SPA if there is no path from start to end
        if path == []:
            self.shortest_path_start = None
            self.shortest_path_add_mode = False
            return None
        else:
            x_list, y_list, parity_list = zip(*path)
            id_numbers = self._model_part.batchCreateVirtualHelices(x_list=x_list,
                                                                    y_list=y_list,
                                                                    parity=parity_list)
            for id_number in id_numbers:
                vhi = self._virtual_helix_item_hash[id_number]
                tool.setVirtualHelixItem(vhi)
                tool.startCreation()
            return id_number
    # end def

    def createToolHoverMove(self, tool, event):
        """Summary

        Args:
            tool (TYPE): Description
            event (TYPE): Description

        Returns:
            TYPE: Description
        """
        is_alt = True if event.modifiers() & Qt.KeyboardModifier.AltModifier else False
        mapped_position = self.griditem.mapFromScene(event.scenePos())
        event_xy = (mapped_position.x(), mapped_position.y())
        if self.griditem.grid_type is GridType.HONEYCOMB:
            event_coord = HoneycombDnaPart.positionModelToLatticeCoord(DEFAULT_RADIUS,
                                                                       event_xy[0],
                                                                       event_xy[1],
                                                                       scale_factor=self.scale_factor,
                                                                       strict=True)
        elif self.griditem.grid_type is GridType.SQUARE:
            event_coord = SquareDnaPart.positionModelToLatticeCoord(DEFAULT_RADIUS,
                                                                    event_xy[0],
                                                                    event_xy[1],
                                                                    scale_factor=self.scale_factor,
                                                                    strict=True)
        else:
            event_coord = None

        self.last_mouse_position = event_xy

        if event_coord:
            try:
                grid_point = self.griditem.points_dict[(event_coord)]
                self.setLastHoveredItem(grid_point)
            except KeyError:
                pass

        # Un-highlight GridItems if necessary by calling createToolHoverLeave
        if len(self._highlighted_path) > 1 or (self._highlighted_path and self._highlighted_path[0] != event_coord):
            self.removeAllCreateHints()

        self._highlighted_grid_point = event_coord
        if event_coord:
            self.griditem.highlightGridPoint(row=event_coord[0], column=event_coord[1], on=True)

        # Highlight GridItems if alt is being held down
        if is_alt and self.shortest_path_add_mode and event_coord is not None:
            self._previewSpa(event_xy)
        else:
            if is_alt and event_coord is not None:
                self.highlightOneGridPoint(self.getLastHoveredCoordinates(), styles.SPA_START_HINT_COLOR)
            elif not is_alt and event_coord is not None:
                part = self._model_part
                next_idnums = (part._getNewIdNum(0), part._getNewIdNum(1))
                self.griditem.showCreateHint(event_coord, next_idnums=next_idnums)
                self._highlighted_path.append(event_coord)

        tool.hoverMoveEvent(self, event)
        return QGraphicsItem.hoverMoveEvent(self, event)
    # end def


    def _previewSpa(self, event_xy):
        """
        Highlight and add VH ID numbers to the GridPoints that the SPA would
        use.

        Args:
            event_xy (tuple):  the x-y coordinates corresponding to the
                position of the mouse

        Returns:
            None
        """
        part = self._model_part
        start_xy = self.shortest_path_start
        end_xy = event_xy
        self._highlighted_path = ShortestPathHelper.shortestPathAStar(start=start_xy ,
                                                                      end=end_xy ,
                                                                      part_radius=DEFAULT_RADIUS,
                                                                      vh_set=self.coordinates_to_vhid.keys(),
                                                                      grid_type=self.griditem.grid_type,
                                                                      scale_factor=self.scale_factor)
        even_id = part._getNewIdNum(0)
        odd_id = part._getNewIdNum(1)
        for coord in self._highlighted_path:
            # This can return True, False or None
            is_odd = self.griditem.showCreateHint(coord, next_idnums=(even_id, odd_id))
            if is_odd is True:
                odd_id += 2
            elif is_odd is False:
                even_id += 2
    # end def

    def createToolHoverLeave(self, tool, event):
        self.removeAllCreateHints()
        return QGraphicsItem.hoverLeaveEvent(self, event)
    # end def

    def selectToolHoverEnter(self, tool, event):
        """
        Hint vh coords that will be created if clipboard is pasted at hoverEnter
        position.
        """
        if tool.clipboard is None:  # is there anything on the clipboard?
            return

        self.removeAllCopyPasteHints()
        event_pos = self.griditem.mapFromScene(event.scenePos())

        positionToLatticeCoord = HoneycombDnaPart.positionModelToLatticeCoord\
            if self.griditem.grid_type is GridType.HONEYCOMB else SquareDnaPart.positionModelToLatticeCoord
        hov_row, hov_col = positionToLatticeCoord(DEFAULT_RADIUS,
                                                  event_pos.x(),
                                                  event_pos.y(),
                                                  self.scale_factor)
        self._last_hovered_coord = (hov_row, hov_col)
        parity = self._getCoordinateParity(hov_row, hov_col)

        part = self._model_part
        vh_id_list = tool.clipboard['vh_list']
        try:
            min_id_same_parity = int(min(filter(lambda x: x[0] % 2 == parity, vh_id_list))[0])
        except ValueError:  # no vhs match parity
            return

        min_pos = part.locationQt(min_id_same_parity, self.scaleFactor())
        min_row, min_col = positionToLatticeCoord(DEFAULT_RADIUS,
                                                  min_pos[0],
                                                  min_pos[1],
                                                  self.scale_factor)
        id_offset = part.getMaxIdNum() if part.getMaxIdNum() % 2 == 0 else part.getMaxIdNum() + 1

        # placing clipboard's min_id_same_parity on the hovered_coord,
        # hint neighboring coords with offsets corresponding to clipboard vhs
        hinted_coordinates = []
        for i in range(len(vh_id_list)):
            vh_id, vh_len = vh_id_list[i]
            position_xy = part.locationQt(vh_id, self.scaleFactor())
            copied_row, copied_col = positionToLatticeCoord(DEFAULT_RADIUS,
                                                            position_xy[0],
                                                            position_xy[1],
                                                            self.scale_factor)
            hint_coord = (hov_row+(copied_row-min_row), hov_col+(copied_col-min_col))
            hinted_coordinates.append(hint_coord)

        # If any of the highlighted coordinates conflict with any existing VHs, abort
        if any(coord in self.coordinates_to_vhid.keys() for coord in hinted_coordinates):
            self.copypaste_origin_offset = None
            return

        for i, hint_coord in enumerate(hinted_coordinates):
            self.griditem.showCreateHint(hint_coord, next_idnums=(i+id_offset, i+id_offset))
            self._highlighted_copypaste.append(hint_coord)
        # print("clipboard contents:", vh_id_list, min_idnum, idnum_offset)

        hov_x, hov_y = self._getModelXYforCoord(hov_row, hov_col)
        min_x, min_y = part.getVirtualHelixOrigin(min_id_same_parity)

        self.copypaste_origin_offset = (round(hov_x-min_x, 9), round(hov_y-min_y, 9))
    # end def

    def selectToolHoverMove(self, tool, event):
        """
        Hint vh coords that will be created if clipboard is pasted at hoverMove
        position.
        """
        if tool.clipboard is None:  # is there anything on the clipboard?
            return

        isInLatticeCoord = HoneycombDnaPart.isInLatticeCoord if self.griditem.grid_type is GridType.HONEYCOMB \
            else SquareDnaPart.isInLatticeCoord
        event_pos = self.griditem.mapFromScene(event.scenePos())
        event_position_xy = (event_pos.x(), event_pos.y())
        positionToLatticeCoord = HoneycombDnaPart.positionModelToLatticeCoord \
            if self.griditem.grid_type is GridType.HONEYCOMB else SquareDnaPart.positionModelToLatticeCoord
        hover_coordinates = positionToLatticeCoord(DEFAULT_RADIUS,
                                                   event_position_xy[0],
                                                   event_position_xy[1],
                                                   self.scale_factor)

        if self._last_hovered_coord == hover_coordinates or not isInLatticeCoord(radius_tuple=self._RADIUS_TUPLE,
                                                                                 xy_tuple=self.last_mouse_position,
                                                                                 coordinate_tuple=self.getLastHoveredCoordinates(),
                                                                                 scale_factor=self.scale_factor):
            return
        else:
            self._last_hovered_coord = hover_coordinates
            self.removeAllCopyPasteHints()

        parity = self._getCoordinateParity(hover_coordinates[0], hover_coordinates[1])
        vh_id_list = tool.clipboard['vh_list']
        try:
            min_id_same_parity = int(min(filter(lambda x: x[0] % 2 == parity, vh_id_list))[0])
        except ValueError:
            return

        part = self._model_part
        min_pos = part.locationQt(min_id_same_parity, self.scaleFactor())
        min_row, min_col = positionToLatticeCoord(DEFAULT_RADIUS,
                                                  min_pos[0],
                                                  min_pos[1],
                                                  self.scale_factor)

        id_offset = part.getMaxIdNum() if part.getMaxIdNum() % 2 == 0 else part.getMaxIdNum() + 1

        # placing clipboard's min_id_same_parity on the hovered_coord,
        # hint neighboring coords with offsets corresponding to clipboard vhs
        hinted_coordinates = []
        for i in range(len(vh_id_list)):
            vh_id, vh_len = vh_id_list[i]
            position_xy = part.locationQt(vh_id, self.scaleFactor())
            copied_row, copied_col = positionToLatticeCoord(DEFAULT_RADIUS,
                                                            position_xy[0],
                                                            position_xy[1],
                                                            self.scale_factor)
            hint_coord = (hover_coordinates[0]+(copied_row-min_row), hover_coordinates[1]+(copied_col-min_col))
            hinted_coordinates.append(hint_coord)

        # If any of the highlighted coordinates conflict with any existing VHs, abort
        if any(coord in self.coordinates_to_vhid.keys() for coord in hinted_coordinates):
            self.copypaste_origin_offset = None
            return

        for i, hint_coord in enumerate(hinted_coordinates):
            self.griditem.showCreateHint(hint_coord, next_idnums=(i+id_offset, i+id_offset))
            self._highlighted_copypaste.append(hint_coord)

        # This is going to give us the difference between hovering and the min parity location.  We want the
        # difference between the min parity's former and new location
        hov_x, hov_y = self._getModelXYforCoord(hover_coordinates[0], hover_coordinates[1])

        min_x, min_y, _ = part.getCoordinate(min_id_same_parity, 0)
        self.copypaste_origin_offset = (round(hov_x-min_x, 9), round(hov_y-min_y, 9))
    # end def

    def selectToolHoverLeave(self, tool, event):
        self.removeAllCopyPasteHints()
    # end def

    def selectToolMousePress(self, tool, event):
        """
        Args:
            tool (TYPE): Description
            event (TYPE): Description
        """
        if tool.clipboard is not None:
            self.pasteClipboard(tool, event)

        tool.setPartItem(self)
        pt = tool.eventToPosition(self, event)
        part_pt_tuple = self.getModelPos(pt)
        part = self._model_part
        if part.isVirtualHelixNearPoint(part_pt_tuple):
            id_num = part.getVirtualHelixAtPoint(part_pt_tuple)
            if id_num is not None:
                pass
                # loc = part.getCoordinate(id_num, 0)
                # print("VirtualHelix #{} at ({:.3f}, {:.3f})".format(id_num, loc[0], loc[1]))
            else:
                # tool.deselectItems()
                tool.modelClear()
        else:
            # tool.deselectItems()
            tool.modelClear()
        return QGraphicsItem.mousePressEvent(self, event)
    # end def

    def pasteClipboard(self, tool, event):
        assert tool.clipboard is not None
        assert isinstance(event, QGraphicsSceneMouseEvent)

        new_vhs = tool.pasteClipboard()

    def removeAllCopyPasteHints(self):
        if self.lock_hints:
            return
        for coord in self._highlighted_copypaste:
            self.griditem.showCreateHint(coord, show_hint=False)
        self._highlighted_copypaste = []
        self.copypaste_origin_offset = None
    # end def

    def removeAllCreateHints(self):
        """
        Remove the create hints from each currently hinted GridItem.

        Iterates over all coordinates in self._highlighted_path.

        Returns:
            None
        """
        if self.lock_hints:
            return
        for coord in self._highlighted_path:
            self.griditem.showCreateHint(coord, show_hint=False)
            self.griditem.highlightGridPoint(coord[0],
                                             coord[1],
                                             on=False)
        self._highlighted_path = []
    # end def

    def highlightOneGridPoint(self, coordinates, color=None):
        """
        Add a hint to one GridPoint.

        Args:
            coordinates (tuple):  the row-column coordinates of the gridPoint to
                be highlighted
            color ():  the color that the gridPoint should be changed to

        Returns:
            None
        """
        if coordinates is None:
            return

        assert isinstance(coordinates, tuple) and len(coordinates) is 2
        assert isinstance(coordinates[0], int) and isinstance(coordinates[1], int)

        next_idnums = (self._model_part._getNewIdNum(0), self._model_part._getNewIdNum(1))
        self.griditem.showCreateHint(coordinates, next_idnums=next_idnums, color=color)
        self._highlighted_path.append(coordinates)
    # end def

    def getLastHoveredCoordinates(self):
        """
        Get the row and column corresponding to the GridPoint that was most
        recently hovered over.

        This accounts for the fact that the rows are inverted (i.e. the result
        returned by this method will match the coordinate system stored in this
        class' internal records of coordinates)

        Returns:
            A tuple corresponding to the row and column of the most recently
                hovered GridPoint.

        """
        if self._last_hovered_item:
            row, column = self._last_hovered_item.coord()
            return -row, column
    # end def
# end class
