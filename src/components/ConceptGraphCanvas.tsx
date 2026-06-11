import cytoscape, {
  type Core,
  type EdgeDefinition,
  type NodeDefinition,
  type NodeSingular,
  type EdgeSingular,
} from "cytoscape";
import { useEffect, useRef } from "react";

export interface GraphCanvasNode {
  id: string;
  label: string;
  type?: string;
  description?: string;
}

export interface GraphCanvasEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

interface ConceptGraphCanvasProps {
  nodes: GraphCanvasNode[];
  edges: GraphCanvasEdge[];
}

export default function ConceptGraphCanvas({
  nodes,
  edges,
}: ConceptGraphCanvasProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  // Initialize Cytoscape instance once
  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      minZoom: 0.35,
      maxZoom: 2.4,
      wheelSensitivity: 0.18,
      layout: {
        name: "cose",
        animate: false,
        idealEdgeLength: 140,
        nodeRepulsion: 7000,
        nestingFactor: 0.8,
      },
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#ffffff",
            "border-color": "#2f6f73",
            "border-width": 2,
            color: "#172033",
            "font-size": 12,
            "font-weight": 600,
            height: "44px",
            label: "data(label)",
            "min-zoomed-font-size": 8,
            "overlay-opacity": 0,
            shape: "round-rectangle",
            "text-halign": "center",
            "text-max-width": "116px",
            "text-valign": "center",
            "text-wrap": "wrap",
            width: "132px",
          },
        },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "font-size": 10,
            "line-color": "#a7b5c1",
            "target-arrow-color": "#a7b5c1",
            "target-arrow-shape": "triangle",
            "text-background-color": "#f7f9fb",
            "text-background-opacity": 0.8,
            "text-background-padding": "3px",
            width: 2,
          },
        },
        {
          selector: "node.selectedPath",
          style: {
            "background-color": "#dbf7f2",
            "border-color": "#0d9488",
            "border-width": 4,
            color: "#0f3f46",
          },
        },
        {
          selector: "edge.selectedPath",
          style: {
            "line-color": "#0d9488",
            "target-arrow-color": "#0d9488",
            width: 4,
          },
        },
        {
          selector: ".dimmed",
          style: {
            opacity: 0.28,
          },
        },
      ],
    });

    cyRef.current = cy;
    cy.userZoomingEnabled(true);
    cy.userPanningEnabled(true);
    cy.nodes().grabify();

    cy.on("tap", "node", async (event) => {
      const selected = event.target as NodeSingular;

      // Reset and dim everything first
      cy.elements().removeClass("selectedPath dimmed");
      cy.elements().addClass("dimmed");

      // Animated traversal: follow the first outgoing link repeatedly
      // This makes linear prerequisite chains feel alive.
      try {
        const maxSteps = 50;
        let current: NodeSingular | null = selected;

        // mark start node
        current.removeClass("dimmed").addClass("selectedPath");

        for (let step = 0; step < maxSteps && current; step++) {
          const outEdges = current.outgoers("edge");
          if (outEdges.length === 0) break;

          // Prefer an edge that leads to an unvisited node
          let edge = (outEdges.filter((e) => !( (e as EdgeSingular).target().hasClass("selectedPath") ))[0] ?? outEdges[0]) as EdgeSingular;
          const targetNode = edge.target() as NodeSingular;

          // reveal and animate the edge
          edge.removeClass("dimmed");
          await new Promise<void>((resolve) => {
            // Use any cast because animate options typing varies between versions
            (edge as any).animate(
              {
                style: {
                  "line-color": "#0d9488",
                  "target-arrow-color": "#0d9488",
                  width: 4,
                },
              },
              { duration: 400, complete: () => resolve() }
            );
          });

          // lock-in selected style on edge and reveal the node
          edge.addClass("selectedPath");
          targetNode.removeClass("dimmed").addClass("selectedPath");

          // small pause before continuing to next hop
          await new Promise((r) => setTimeout(r, 150));

          // advance
          current = targetNode;
        }
      } catch (err) {
        // Fallback: instant highlight if animation fails
        const outgoingEdges = selected.outgoers("edge");
        const outgoingNodes = selected.outgoers("node");
        selected.removeClass("dimmed").addClass("selectedPath");
        outgoingEdges.removeClass("dimmed").addClass("selectedPath");
        outgoingNodes.removeClass("dimmed").addClass("selectedPath");
      }
    });

    cy.on("tap", (event) => {
      if (event.target === cy) {
        cy.elements().removeClass("selectedPath dimmed");
      }
    });

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, []); // Run once on mount

  // Update data when nodes or edges change
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    cy.elements().remove(); // Clear previous data

    if (nodes.length === 0 && edges.length === 0) {
      return;
    }

    const elements: Array<NodeDefinition | EdgeDefinition> = [
      ...nodes.map((node) => ({
        data: {
          id: node.id,
          label: node.label,
          type: node.type ?? "concept",
          description: node.description ?? "",
        },
      })),
      ...edges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.label ?? "",
        },
      })),
    ];

    cy.add(elements);
    
    // Re-run layout to position new nodes correctly
    cy.layout({
      name: "cose",
      animate: true,
      idealEdgeLength: 140,
      nodeRepulsion: 7000,
      nestingFactor: 0.8,
    }).run();
    
    // Fit viewport to graph
    cy.fit(undefined, 50);

  }, [nodes, edges]);

  return (
    <div className="relative h-full min-h-[480px] overflow-hidden rounded-md border border-slate-200 bg-panel">
      {nodes.length === 0 ? (
        <div className="flex h-full min-h-[480px] items-center justify-center px-8 text-center text-sm text-slate-500">
          Ask a question to load the conceptual prerequisite map.
        </div>
      ) : null}
      <div ref={containerRef} className="absolute inset-0" />
    </div>
  );
}
