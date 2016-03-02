# Copyright 2016 - Alcatel-Lucent
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.


import copy
import json

import networkx as nx
from networkx.readwrite import json_graph

from oslo_log import log as logging

from elements import Edge
from elements import Vertex
from graph import Direction
from graph import Graph
from notifier import Notifier
from vitrage.common.constants import VertexProperties as VProps
from vitrage.graph.filter import check_filter
from vitrage.graph.query import create_predicate

LOG = logging.getLogger(__name__)


def edge_copy(source_id, target_id, label, data):
    return Edge(source_id=source_id, target_id=target_id,
                label=label, properties=copy.copy(data))


def vertex_copy(v_id, data):
    return Vertex(vertex_id=v_id, properties=copy.copy(data))


class NXGraph(Graph):

    GRAPH_TYPE = "networkx"

    def __init__(self, name='networkx_graph', root_id=None):
        self._g = nx.MultiDiGraph()
        self.root_id = root_id
        super(NXGraph, self).__init__(name=name, graph_type=NXGraph.GRAPH_TYPE)

    def __len__(self):
        return len(self._g)

    def copy(self):
        self_copy = NXGraph(self.name, self.root_id)
        self_copy._g = self._g.copy()
        return self_copy

    @Notifier.add_notify
    def add_vertex(self, v):
        """Add a vertex to the graph

        :type v: Vertex
        """
        # Call a private method, so to separate the notifier from logic
        self._add_vertex(v)

    def _add_vertex(self, v):
        super(NXGraph, self).add_vertex(v=v)
        properties_copy = copy.copy(v.properties)
        self._g.add_node(n=v.vertex_id, attr_dict=properties_copy)

    @Notifier.add_notify
    def add_edge(self, e):
        """Add an edge to the graph

        :type e: Edge
        """
        # Call a private method, so to separate the notifier from logic
        self._add_edge(e)

    def _add_edge(self, e):
        properties_copy = copy.copy(e.properties)
        self._g.add_edge(u=e.source_id, v=e.target_id,
                         key=e.label, attr_dict=properties_copy)

    def get_vertex(self, v_id):
        """Fetch a vertex from the graph

        :rtype: Vertex
        """
        properties = self._g.node.get(v_id, None)
        if properties is not None:
            return vertex_copy(v_id, properties)
        LOG.debug("get_vertex item not found. v_id=%s", str(v_id))
        return None

    def get_edge(self, source_id, target_id, label):
        try:
            properties = self._g.adj[source_id][target_id][label]
        except KeyError:
            LOG.debug("get_edge item not found. source_id=%s, target_id=%s, "
                      "label=%s", str(source_id), str(target_id), str(label))
            return None
        if properties is not None:
            return edge_copy(source_id, target_id, label, properties)
        return None

    def get_edges(self, v_id, direction=Direction.BOTH,
                  attr_filter=None):
        """Fetch multiple edges from the graph

        :rtype: list of Edge
        """
        def check_edge(edge_data):
            return check_filter(edge_data, attr_filter)

        nodes, edges = self._neighboring_nodes_edges_query(
            v_id, edge_predicate=check_edge, direction=direction)
        edge_copies = [edge_copy(u, v, label, data)
                       for u, v, label, data in edges]
        return edge_copies

    def _get_edges_by_direction(self, v_id, direction):
        """Get all the edges from the vertex according to the direction

        :return: all the neighboring edges that match the filter
        :rtype: list of tuples (source_id, target_id, label, data)
        """
        if direction == Direction.BOTH:
            edges = []
            edges.extend(self._get_edges_by_direction(v_id, Direction.IN))
            edges.extend(self._get_edges_by_direction(v_id, Direction.OUT))
            return edges
        if direction == Direction.OUT:
            return self._g.out_edges(nbunch=v_id, data=True, keys=True)
        else:  # IN
            return self._g.in_edges(nbunch=v_id, data=True, keys=True)

    def num_vertices(self):
        return len(self._g)

    def num_edges(self):
        return self._g.number_of_edges()

    @Notifier.update_notify
    def update_vertex(self, v, hard_update=False):
        """Update the vertex properties

        :type v: Vertex
        """
        orig_prop = self._g.node.get(v.vertex_id, None)
        if orig_prop and hard_update:
            orig_prop.clear()
        v.properties = self._update_properties(orig_prop, v.properties)
        self._add_vertex(v)

    @Notifier.update_notify
    def update_edge(self, e, hard_update=False):
        """Update the edge properties

        :type e: Edge
        """
        orig_prop = self._g.edge.get(
            e.source_id, {}).get(
            e.target_id, {}).get(
            e.label, None)
        if orig_prop and hard_update:
            orig_prop.clear()
        e.properties = self._update_properties(orig_prop, e.properties)
        self._add_edge(e)

    @staticmethod
    def _update_properties(orig_props, new_props):
        if orig_props is None:
            orig_props = dict()
        keys_to_remove = [key for key, val in new_props.items() if val is None]
        for key in keys_to_remove:
            del new_props[key]
            if key in orig_props:
                del orig_props[key]
        orig_props.update(new_props)
        return orig_props

    def remove_vertex(self, v):
        """Remove Vertex v and its edges from the graph

        :type v: Vertex
        """
        self._g.remove_node(n=v.vertex_id)

    def remove_edge(self, e):
        """Remove an edge from the graph

        :type e: Edge
        """
        self._g.remove_edge(u=e.source_id, v=e.target_id, key=e.label)

    def get_vertices(self, vertex_attr_filter=None, query_dict=None):
        def check_vertex((v_id, vertex_data)):
            return check_filter(vertex_data, vertex_attr_filter)

        if not query_dict:
            items = filter(check_vertex, self._g.nodes_iter(data=True))
            return [vertex_copy(node, node_data) for node, node_data in items]
        elif not vertex_attr_filter:
            vertices = []
            match_func = create_predicate(query_dict)
            for node, node_data in self._g.nodes_iter(data=True):
                v = vertex_copy(node, node_data)
                if match_func(v):
                    vertices.append(v)
            return vertices
        else:
            return []

    def neighbors(self, v_id, vertex_attr_filter=None, edge_attr_filter=None,
                  direction=Direction.BOTH):

        def check_edge(edge_data):
            return check_filter(edge_data, edge_attr_filter)

        def check_vertex(vertex_data):
            return check_filter(vertex_data, vertex_attr_filter)

        nodes, edges = self._neighboring_nodes_edges_query(
            v_id=v_id, vertex_predicate=check_vertex,
            edge_predicate=check_edge, direction=direction)
        vertices = [vertex_copy(n, data) for n, data in nodes]
        return vertices

    def _neighboring_nodes_edges_query(self, v_id,
                                       vertex_predicate=None,
                                       edge_predicate=None,
                                       direction=Direction.BOTH,
                                       ):
        if not direction:
            LOG.error("_neighboring_nodes_edges: direction cannot be None")
            raise AttributeError("neighbors: direction cannot be None")

        if not v_id:
            LOG.error("_neighboring_nodes_edges: v_id cannot be None")
            raise AttributeError("neighbors: v_id cannot be None")

        edges = self._get_edges_by_direction(v_id, direction)
        edges_filtered1 = []
        for edge in edges:
            if not edge_predicate or edge_predicate(edge[3]):
                edges_filtered1.append(edge)

        edges_filtered2 = []
        nodes = []

        for source_id, target_id, label, data in edges_filtered1:
            node_id_to_test = source_id if target_id == v_id else target_id
            node_data = self._g.node[node_id_to_test]
            if not vertex_predicate or vertex_predicate(node_data):
                edges_filtered2.append((source_id, target_id, label, data))
                nodes.append((node_id_to_test, node_data))
        return nodes, edges_filtered2

    def output_graph(self):
        node_link_data = json_graph.node_link_data(self._g)
        for node in node_link_data['nodes']:
            if VProps.ID in self._g.node[node[VProps.ID]]:
                node[VProps.ID] = self._g.node[node[VProps.ID]][VProps.ID]
        return json.dumps(node_link_data)