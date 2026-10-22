import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { RoundedBox } from "@react-three/drei";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
    ACESFilmicToneMapping,
    BoxGeometry,
    CanvasTexture,
    CylinderGeometry,
    Group,
    BackSide,
    Mesh,
    MeshBasicMaterial,
    MeshStandardMaterial,
    PMREMGenerator,
    Scene,
    SRGBColorSpace,
} from "three";

type Detail = "full" | "still" | "lite";

type Tilt = { x: number; y: number };

const sceneClock = { t: 1.2, paused: false };

export default function FinanceUniverse() {
    const host = useRef<HTMLDivElement>(null);
    const tilt = useRef<Tilt>({ x: 0, y: 0 });
    const detail = useDetail();
    const [shown, setShown] = useState(true);
    const [live, setLive] = useState(false);

    useEffect(() => {
        setLive(true);
    }, []);

    useEffect(() => {
        const node = host.current;
        if (!node) return;
        const observer = new IntersectionObserver(
            ([entry]) => setShown(entry?.isIntersecting ?? false),
            { threshold: 0.15 },
        );
        observer.observe(node);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (!shown || !live || detail === "lite") return;
        const kick = window.setTimeout(() => window.dispatchEvent(new Event("resize")), 50);
        return () => window.clearTimeout(kick);
    }, [shown, detail, live]);

    if (detail === "lite") {
        return (
            <div className="relative h-full min-h-[420px] w-full">
                <StaticStage />
            </div>
        );
    }

    return (
        <div
            ref={host}
            className="relative h-full min-h-[420px] w-full"
            onPointerMove={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect();
                tilt.current.x = ((event.clientY - bounds.top) / bounds.height - 0.5) * -1;
                tilt.current.y = (event.clientX - bounds.left) / bounds.width - 0.5;
            }}
            onPointerLeave={() => {
                tilt.current.x = 0;
                tilt.current.y = 0;
            }}
        >
            {shown && live ? (
                <Canvas
                    dpr={[1, 1.6]}
                    camera={{ position: [0.05, 0.04, 10.2], fov: 32 }}
                    frameloop="always"
                    gl={{
                        antialias: true,
                        alpha: true,
                        powerPreference: "high-performance",
                        stencil: false,
                        preserveDrawingBuffer: true,
                    }}
                    style={{ background: "transparent" }}
                >
                    <KeepAlive />
                    {detail === "still" ? <PaintOnce /> : null}
                    <Stage tilt={tilt} paused={detail === "still"} />
                </Canvas>
            ) : (
                <StaticStage />
            )}
        </div>
    );
}

function initialDetail(): Detail {
    if (typeof window === "undefined") return "full";
    if (window.matchMedia("(max-width: 1023px)").matches) return "lite";
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return "still";
    return "full";
}

function useDetail(): Detail {
    const [detail, setDetail] = useState<Detail>(initialDetail);
    useEffect(() => {
        const narrow = window.matchMedia("(max-width: 1023px)");
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
        const apply = () => {
            if (narrow.matches) setDetail("lite");
            else if (reduce.matches) setDetail("still");
            else setDetail("full");
        };
        apply();
        narrow.addEventListener("change", apply);
        reduce.addEventListener("change", apply);
        return () => {
            narrow.removeEventListener("change", apply);
            reduce.removeEventListener("change", apply);
        };
    }, []);
    return detail;
}

function KeepAlive() {
    const set = useThree((state) => state.set);
    const invalidate = useThree((state) => state.invalidate);
    useEffect(() => {
        const wake = () => {
            set((state) => ({ internal: { ...state.internal, active: true } }));
            invalidate();
        };
        wake();
        const later = window.setTimeout(wake, 300);
        return () => window.clearTimeout(later);
    }, [set, invalidate]);
    return null;
}

function PaintOnce() {
    const invalidate = useThree((state) => state.invalidate);
    useEffect(() => {
        invalidate();
        const later = window.setTimeout(invalidate, 200);
        const again = window.setTimeout(invalidate, 700);
        return () => {
            window.clearTimeout(later);
            window.clearTimeout(again);
        };
    }, [invalidate]);
    return null;
}

function Stage({ tilt, paused }: { tilt: RefObject<Tilt>; paused: boolean }) {
    const { camera } = useThree();
    sceneClock.paused = false;
    useFrame(({ clock }) => {
        sceneClock.t = clock.elapsedTime * (paused ? 0.4 : 1);
        const t = sceneClock.t;
        const aim = tilt.current;
        const sway = paused ? 0.08 : 0.22;
        const x = 0.2 + Math.sin(t * 0.16) * sway + (aim?.y ?? 0) * 0.28;
        const y = 0.06 + Math.sin(t * 0.12) * sway * 0.45 + (aim?.x ?? 0) * 0.16;
        camera.position.x += (x - camera.position.x) * 0.05;
        camera.position.y += (y - camera.position.y) * 0.05;
        camera.lookAt(0.15, 0, 0);
    });
    return (
        <>
            <ClearBackground />
            <Studio />
            <ambientLight intensity={0.5} />
            <directionalLight position={[3.5, 5, 6]} intensity={2.4} color="#F4F7FF" />
            <directionalLight position={[-3, 1.5, 3]} intensity={0.7} color="#9BB6FF" />
            <Assistant />
            <BankCard
                phase={0.2}
                scale={0.66}
                home={{ x: 0.55, y: 0.12, z: 0.25, rx: 0.08, ry: 0.18 }}
                alt={{ x: 0.7, y: 0.22, z: 0.05, rx: 0.02, ry: 0.05 }}
                face={["#10244f", "#315BEA", "#1a2d66"]}
                edge="#0c1a38"
                network="visa"
                last4="4821"
            />
            <BankCard
                phase={2.4}
                scale={0.48}
                home={{ x: 1.4, y: 0.92, z: -0.25, rx: -0.1, ry: -0.32 }}
                alt={{ x: 1.2, y: 1.05, z: -0.45, rx: -0.02, ry: -0.1 }}
                face={["#3A2468", "#7B5CFF", "#24143F"]}
                edge="#1A1030"
                network="rupay"
                last4="7360"
                hologram
            />
            <Coin color="#FFE08A" symbol="₹" radius={0.16} path={{ speed: 0.8, phase: 0.6, rest: [-0.95, -0.35, 0.45] }} />
            <Coin color="#F7FBFF" symbol="$" radius={0.2} path={{ speed: 0.65, phase: 1.4, rest: [0.2, 1.15, 0.35] }} />
            <Coin color="#FFE7A3" symbol="$" radius={0.15} path={{ speed: -0.75, phase: 3.1, rest: [1.35, -0.15, 0.5] }} />
            <Bars />
            <Donut />
            <Arrows />
        </>
    );
}

function Studio() {
    const { gl, scene, invalidate } = useThree();
    useEffect(() => {
        gl.toneMapping = ACESFilmicToneMapping;
        gl.toneMappingExposure = 1.28;
        const pmrem = new PMREMGenerator(gl);
        const studio = new Scene();
        const shell = new Mesh(
            new BoxGeometry(12, 12, 12),
            new MeshBasicMaterial({ color: "#05070F", side: BackSide }),
        );
        studio.add(shell);
        const glow = (color: string, x: number, y: number, z: number, size: number) => {
            const mesh = new Mesh(new BoxGeometry(size, size, size), new MeshBasicMaterial({ color }));
            mesh.position.set(x, y, z);
            studio.add(mesh);
        };
        glow("#4D7CFF", -3.2, 2.2, -2, 2.2);
        glow("#70E7FF", 2.4, 0.4, 2.2, 1.1);
        glow("#9B8CFF", 0.2, -2.4, -3, 1.6);
        glow("#E8C477", 2.8, 2.6, 1.2, 0.7);
        glow("#F7FAFF", -0.4, 3.4, 3.2, 0.8);
        try {
            const map = pmrem.fromScene(studio, 0.05).texture;
            scene.environment = map;
            return () => {
                map.dispose();
                pmrem.dispose();
            };
        } finally {
            invalidate();
        }
    }, [gl, scene, invalidate]);
    return null;
}

function ClearBackground() {
    const { scene } = useThree();
    useEffect(() => {
        scene.background = null;
    }, [scene]);
    return null;
}

function Backdrop() {
    const map = useMemo(
        () =>
            canvasTexture((context, size) => {
                const glow = context.createRadialGradient(size * 0.5, size * 0.48, 10, size * 0.5, size * 0.5, size * 0.5);
                glow.addColorStop(0, "#1A2A55");
                glow.addColorStop(0.35, "#0C1428");
                glow.addColorStop(0.7, "#05070F");
                glow.addColorStop(1, "#05070F");
                context.fillStyle = glow;
                context.fillRect(0, 0, size, size);
            }, 512),
        [],
    );
    return (
        <mesh position={[0.4, -0.1, -2.2]}>
            <planeGeometry args={[6.4, 4.6]} />
            <meshBasicMaterial map={map} depthWrite={false} />
        </mesh>
    );
}

function Assistant() {
    const ref = useRef<Group>(null);
    const bubble = useMemo(() => speechTexture(), []);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        group.position.y = -0.05 + Math.sin(t * 1.3) * 0.1;
        group.rotation.y = -0.25 + Math.sin(t * 0.6) * 0.18;
    });
    const shell = "#8FDFF2";
    const body = "#7ED99A";
    const limb = "#6BC98A";
    return (
        <group ref={ref} position={[-1.35, -0.35, 0.45]} scale={0.62}>
            {[-0.22, 0.22].map((x) => (
                <group key={x} position={[x, 1.78, 0]}>
                    <mesh>
                        <cylinderGeometry args={[0.018, 0.018, 0.28, 8]} />
                        <meshStandardMaterial color={shell} />
                    </mesh>
                    <mesh position={[0, 0.18, 0]}>
                        <sphereGeometry args={[0.05, 12, 12]} />
                        <meshStandardMaterial color="#C6F26A" emissive="#B6F26A" emissiveIntensity={0.6} />
                    </mesh>
                </group>
            ))}
            <RoundedBox args={[0.78, 0.66, 0.58]} radius={0.18} smoothness={4} position={[0, 1.22, 0]}>
                <meshStandardMaterial color={shell} roughness={0.32} metalness={0.08} />
            </RoundedBox>
            <mesh position={[0, 1.16, 0.3]}>
                <boxGeometry args={[0.5, 0.34, 0.04]} />
                <meshStandardMaterial color="#102033" />
            </mesh>
            <mesh position={[-0.1, 1.18, 0.34]}>
                <capsuleGeometry args={[0.045, 0.08, 6, 10]} />
                <meshStandardMaterial color="#B8FBFF" emissive="#7EEBFF" emissiveIntensity={1.6} />
            </mesh>
            <mesh position={[0.1, 1.18, 0.34]}>
                <capsuleGeometry args={[0.045, 0.08, 6, 10]} />
                <meshStandardMaterial color="#B8FBFF" emissive="#7EEBFF" emissiveIntensity={1.6} />
            </mesh>
            <mesh position={[0, 1.04, 0.34]} rotation={[0, 0, 0]}>
                <torusGeometry args={[0.06, 0.012, 8, 16, Math.PI]} />
                <meshStandardMaterial color="#7DEEC8" emissive="#7DEEC8" emissiveIntensity={0.8} />
            </mesh>
            <mesh position={[-0.46, 1.22, 0]} rotation={[0, 0, Math.PI / 2]}>
                <cylinderGeometry args={[0.16, 0.16, 0.1, 18]} />
                <meshStandardMaterial color={shell} />
            </mesh>
            <mesh position={[-0.52, 1.22, 0]} rotation={[0, Math.PI / 2, 0]}>
                <torusGeometry args={[0.13, 0.028, 8, 18]} />
                <meshStandardMaterial color="#C6F26A" emissive="#9BE05A" emissiveIntensity={0.25} />
            </mesh>
            <mesh position={[0.46, 1.22, 0]} rotation={[0, 0, Math.PI / 2]}>
                <cylinderGeometry args={[0.16, 0.16, 0.1, 18]} />
                <meshStandardMaterial color={shell} />
            </mesh>
            <mesh position={[0.52, 1.22, 0]} rotation={[0, Math.PI / 2, 0]}>
                <torusGeometry args={[0.13, 0.028, 8, 18]} />
                <meshStandardMaterial color="#C6F26A" emissive="#9BE05A" emissiveIntensity={0.25} />
            </mesh>
            <RoundedBox args={[0.7, 0.58, 0.46]} radius={0.14} smoothness={3} position={[0, 0.52, 0]}>
                <meshStandardMaterial color={body} roughness={0.34} />
            </RoundedBox>
            <mesh position={[0, 0.58, 0.24]}>
                <boxGeometry args={[0.22, 0.12, 0.02]} />
                <meshStandardMaterial color="#DFFFE8" />
            </mesh>
            <mesh position={[-0.42, 0.42, 0.08]} rotation={[0, 0, 0.5]}>
                <capsuleGeometry args={[0.06, 0.22, 6, 8]} />
                <meshStandardMaterial color={limb} />
            </mesh>
            <mesh position={[0.42, 0.42, 0.08]} rotation={[0, 0, -0.5]}>
                <capsuleGeometry args={[0.06, 0.22, 6, 8]} />
                <meshStandardMaterial color={limb} />
            </mesh>
            <mesh position={[-0.22, 0.02, 0.16]} rotation={[0.4, 0, 0.7]}>
                <capsuleGeometry args={[0.07, 0.28, 6, 8]} />
                <meshStandardMaterial color={body} />
            </mesh>
            <mesh position={[0.24, 0.02, 0.16]} rotation={[0.4, 0, -0.7]}>
                <capsuleGeometry args={[0.07, 0.28, 6, 8]} />
                <meshStandardMaterial color={body} />
            </mesh>
            <mesh position={[-0.38, -0.16, 0.28]} rotation={[0.2, 0, 0.2]}>
                <boxGeometry args={[0.18, 0.08, 0.26]} />
                <meshStandardMaterial color="#9FD4F2" />
            </mesh>
            <mesh position={[0.4, -0.16, 0.28]} rotation={[0.2, 0, -0.2]}>
                <boxGeometry args={[0.18, 0.08, 0.26]} />
                <meshStandardMaterial color="#9FD4F2" />
            </mesh>
            <mesh position={[0.95, 1.28, 0.15]}>
                <planeGeometry args={[0.78, 0.62]} />
                <meshBasicMaterial map={bubble} transparent />
            </mesh>
        </group>
    );
}

function Piggy() {
    const ref = useRef<Group>(null);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        group.position.y = -1.02 + Math.sin(t * 1.1) * 0.06;
        group.rotation.y = 0.45 + Math.sin(t * 0.45) * 0.12;
    });
    return (
        <group ref={ref} position={[1.4, -1.02, 0.4]}>
            <mesh scale={[1.2, 0.86, 0.78]}>
                <sphereGeometry args={[0.26, 24, 24]} />
                <meshStandardMaterial color="#E7A3C0" metalness={0.28} roughness={0.38} />
            </mesh>
            <mesh position={[-0.16, 0.2, 0]} rotation={[0, 0, 0.4]}>
                <sphereGeometry args={[0.08, 12, 12]} />
                <meshStandardMaterial color="#D489A8" roughness={0.4} />
            </mesh>
            <mesh position={[0.16, 0.2, 0]} rotation={[0, 0, -0.4]}>
                <sphereGeometry args={[0.08, 12, 12]} />
                <meshStandardMaterial color="#D489A8" roughness={0.4} />
            </mesh>
            <mesh position={[0.22, 0.02, 0.16]} rotation={[Math.PI / 2, 0, 0]}>
                <cylinderGeometry args={[0.07, 0.08, 0.08, 16]} />
                <meshStandardMaterial color="#F2C2D4" roughness={0.35} />
            </mesh>
            <mesh position={[0, 0.22, 0]}>
                <boxGeometry args={[0.16, 0.02, 0.04]} />
                <meshStandardMaterial color="#1A2236" metalness={0.6} roughness={0.3} />
            </mesh>
            {[-0.12, 0.12].map((x) =>
                [-0.08, 0.1].map((z) => (
                    <mesh key={`${x}-${z}`} position={[x, -0.24, z]}>
                        <cylinderGeometry args={[0.035, 0.04, 0.08, 8]} />
                        <meshStandardMaterial color="#C97B9A" roughness={0.4} />
                    </mesh>
                )),
            )}
        </group>
    );
}

function Core() {
    const ref = useRef<Group>(null);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        group.position.y = Math.sin(sceneClock.t * 1.1) * 0.06;
    });
    return (
        <group ref={ref} position={[0.2, 0.05, -0.85]}>
            <mesh>
                <sphereGeometry args={[0.42, 32, 32]} />
                <meshStandardMaterial color="#8FB4FF" emissive="#3B6CFF" emissiveIntensity={0.7} metalness={0.2} roughness={0.2} />
            </mesh>
        </group>
    );
}

function Vault() {
    const door = useRef<Group>(null);
    const coins = useRef<Group>(null);
    useFrame(() => {
        const t = sceneClock.t;
        if (door.current) door.current.rotation.y = -0.2 - Math.abs(Math.sin(t * 0.9)) * 0.8;
        coins.current?.children.forEach((child, index) => {
            const progress = (t * 0.4 + index * 0.34) % 1;
            child.position.set(-0.55 + progress * 0.7, 0.22 - progress * 0.08, 0.2);
            child.rotation.x = progress * 6;
        });
    });
    return (
        <group position={[-1.55, -1.15, 0.15]}>
            <RoundedBox args={[0.72, 0.56, 0.46]} radius={0.08} smoothness={3}>
                <meshPhysicalMaterial color="#B7D4FF" transmission={0.72} roughness={0.06} thickness={0.35} transparent opacity={0.8} />
            </RoundedBox>
            <group ref={door} position={[0.38, 0, 0]}>
                <mesh>
                    <boxGeometry args={[0.05, 0.5, 0.4]} />
                    <meshStandardMaterial color="#FFE08A" metalness={0.95} roughness={0.18} emissive="#E0B15A" emissiveIntensity={0.35} />
                </mesh>
            </group>
            <group ref={coins}>
                {[0, 1, 2].map((index) => (
                    <mesh key={index} rotation={[Math.PI / 2, 0, 0]}>
                        <cylinderGeometry args={[0.08, 0.08, 0.025, 16]} />
                        <meshStandardMaterial color="#FFE08A" metalness={1} roughness={0.15} emissive="#E0B15A" emissiveIntensity={0.2} />
                    </mesh>
                ))}
            </group>
        </group>
    );
}

function Shield() {
    const ring = useRef<Mesh>(null);
    const card = useRef<Group>(null);
    useFrame(() => {
        const t = sceneClock.t;
        if (ring.current) ring.current.rotation.z = t * 0.9;
        if (card.current) card.current.rotation.y = Math.sin(t * 0.8) * 0.7;
    });
    return (
        <group position={[1.62, 0.72, -0.15]}>
            <mesh scale={[0.5, 0.66, 0.1]}>
                <sphereGeometry args={[1, 24, 24]} />
                <meshPhysicalMaterial color="#C5E4FF" transmission={0.75} roughness={0.05} transparent opacity={0.4} />
            </mesh>
            <mesh ref={ring}>
                <torusGeometry args={[0.7, 0.014, 8, 36]} />
                <meshStandardMaterial color="#2EBF8A" emissive="#1FAE86" emissiveIntensity={0.7} />
            </mesh>
            <group ref={card}>
                <RoundedBox args={[0.62, 0.4, 0.035]} radius={0.04} smoothness={2}>
                    <meshStandardMaterial color="#1C3F8C" metalness={0.55} roughness={0.28} />
                </RoundedBox>
            </group>
        </group>
    );
}

function Percents() {
    const ref = useRef<Group>(null);
    const map = useMemo(() => symbolTexture("%", "#E0B15A"), []);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        group.children.forEach((child, index) => {
            const angle = t * (0.7 + index * 0.2) + index;
            child.position.y = 1.25 + Math.sin(angle) * 0.12;
            child.rotation.y = angle;
            child.rotation.z = Math.sin(angle) * 0.3;
        });
    });
    return (
        <group ref={ref}>
            <mesh position={[-0.15, 1.25, 0.3]}>
                <planeGeometry args={[0.28, 0.28]} />
                <meshStandardMaterial map={map} metalness={0.8} roughness={0.2} emissive="#E0B15A" emissiveIntensity={0.25} transparent />
            </mesh>
            <mesh position={[0.45, 1.32, 0.1]} scale={0.75}>
                <planeGeometry args={[0.28, 0.28]} />
                <meshStandardMaterial map={map} metalness={0.8} roughness={0.2} emissive="#7DEEC8" emissiveIntensity={0.2} transparent />
            </mesh>
        </group>
    );
}

function BankCard({
    phase,
    scale,
    home,
    alt,
    face,
    edge,
    network,
    last4,
    hologram = false,
}: {
    phase: number;
    scale: number;
    home: Pose;
    alt: Pose;
    face: string[];
    edge: string;
    network: "visa" | "rupay";
    last4: string;
    hologram?: boolean;
}) {
    const ref = useRef<Group>(null);
    const front = useMemo(() => cardFace(face, hologram, network, last4), [face, hologram, network, last4]);
    const back = useMemo(() => cardBack(edge), [edge]);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        const mix = sceneClock.paused ? 0.35 : wave(t * 0.18 + phase * 0.17);
        const pose = lerpPose(home, alt, mix);
        const drift = Math.sin(t * 0.85 + phase);
        const bob = Math.cos(t * 0.7 + phase);
        const flip = 0;
        group.position.set(pose.x + drift * 0.2, pose.y + bob * 0.16, pose.z + drift * 0.1);
        group.rotation.set(pose.rx + bob * 0.22, pose.ry + drift * 0.4 + flip * Math.PI, drift * 0.1);
    });
    return (
        <group ref={ref} scale={scale}>
            <RoundedBox args={[2.32, 1.46, 0.05]} radius={0.09} smoothness={4}>
                <meshPhysicalMaterial color={edge} metalness={0.72} roughness={0.22} clearcoat={0.8} clearcoatRoughness={0.18} />
            </RoundedBox>
            <mesh position={[0, 0, 0.028]}>
                <planeGeometry args={[2.18, 1.34]} />
                <meshPhysicalMaterial map={front} metalness={0.28} roughness={0.42} clearcoat={0.35} clearcoatRoughness={0.35} envMapIntensity={0.6} />
            </mesh>
            <mesh position={[0, 0, -0.028]} rotation={[0, Math.PI, 0]}>
                <planeGeometry args={[2.18, 1.34]} />
                <meshStandardMaterial map={back} metalness={0.5} roughness={0.4} />
            </mesh>
            <Chip />
        </group>
    );
}

function Chip() {
    return (
        <group position={[-0.7, 0.22, 0.04]}>
            <mesh>
                <boxGeometry args={[0.34, 0.26, 0.02]} />
                <meshStandardMaterial color="#F0D48A" emissive="#8a6a32" emissiveIntensity={0.25} metalness={1} roughness={0.22} />
            </mesh>
            {[-0.08, 0, 0.08].map((x) =>
                [-0.06, 0.06].map((y) => (
                    <mesh key={`${x}-${y}`} position={[x, y, 0.012]}>
                        <boxGeometry args={[0.05, 0.035, 0.004]} />
                        <meshStandardMaterial color="#8a6a32" metalness={0.8} roughness={0.35} />
                    </mesh>
                )),
            )}
        </group>
    );
}

function Coin({
    color,
    symbol,
    radius,
    path,
}: {
    color: string;
    symbol: string;
    radius: number;
    path: { speed: number; phase: number; rest: [number, number, number] };
}) {
    const ref = useRef<Group>(null);
    const materials = useMemo(() => coinMaterials(symbol, color), [symbol, color]);
    const geometry = useMemo(() => new CylinderGeometry(radius, radius, radius * 0.16, 48), [radius]);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        const angle = path.phase + t * path.speed;
        group.position.set(
            path.rest[0] + Math.cos(angle) * 0.22,
            path.rest[1] + Math.sin(angle) * 0.16,
            path.rest[2] + Math.sin(angle * 0.7) * 0.12,
        );
        const spin = Math.sin(t * (1.35 + Math.abs(path.speed))) * Math.PI;
        group.rotation.set(Math.PI / 2 + spin, 0, Math.sin(angle) * 0.35);
    });
    return (
        <group ref={ref}>
            <mesh geometry={geometry} material={materials} />
            <mesh rotation={[Math.PI / 2, 0, 0]}>
                <torusGeometry args={[radius, radius * 0.045, 8, 40]} />
                <meshStandardMaterial color={color} metalness={1} roughness={0.16} />
            </mesh>
        </group>
    );
}

function Bars() {
    const ref = useRef<Group>(null);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        const presence = sceneClock.paused ? 1 : 0.45 + 0.55 * Math.sin(t * 0.35) ** 2;
        group.scale.setScalar(0.72 + presence * 0.28);
        group.position.set(-0.35 + Math.sin(t * 0.6) * 0.08, -1.22, 0.25);
        group.rotation.y = -0.55 + Math.sin(t * 0.5) * 0.35;
        group.children.forEach((child, index) => {
            const height = 0.45 + Math.abs(Math.sin(t * 1.4 + index * 0.8)) * (0.7 + presence * 0.8);
            child.scale.y = height;
            child.position.y = height * 0.28;
        });
    });
    const columns = [
        { color: "#2F6BFF", glow: "#163A9A" },
        { color: "#1FAE86", glow: "#0C5C48" },
        { color: "#E07A68", glow: "#8A3D32" },
        { color: "#6E63E8", glow: "#322C86" },
        { color: "#E0B15A", glow: "#7A5A1E" },
    ];
    return (
        <group ref={ref}>
            {columns.map((column, index) => (
                <mesh key={column.color} position={[(index - 2) * 0.26, 0, 0]}>
                    <boxGeometry args={[0.16, 0.62, 0.16]} />
                    <meshStandardMaterial
                        color={column.color}
                        emissive={column.glow}
                        emissiveIntensity={0.45}
                        metalness={0.45}
                        roughness={0.28}
                    />
                </mesh>
            ))}
        </group>
    );
}

function Donut() {
    const ref = useRef<Group>(null);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        const presence = sceneClock.paused ? 0.8 : 0.55 + 0.45 * Math.abs(Math.sin(t * 0.7));
        group.position.set(1.5, -0.72 + Math.sin(t * 0.7) * 0.06, 0.15);
        group.rotation.x = 0.55;
        group.rotation.z = t * 0.22;
        const spread = Math.abs(Math.sin(t * 0.45)) * 0.05;
        group.children.forEach((child, index) => {
            const mid = index * (Math.PI / 2) + 0.4;
            child.position.set(Math.cos(mid) * spread, Math.sin(mid) * spread, 0);
        });
        group.scale.setScalar(0.85 + presence * 0.08);
    });
    const span = Math.PI / 2 - 0.05;
    const segments = [
        { color: "#2F6BFF", glow: "#163A9A", start: 0, length: span },
        { color: "#1FAE86", glow: "#0C5C48", start: Math.PI / 2, length: span },
        { color: "#E07A68", glow: "#8A3D32", start: Math.PI, length: span },
        { color: "#E0B15A", glow: "#7A5A1E", start: Math.PI * 1.5, length: span },
    ];
    return (
        <group ref={ref}>
            {segments.map((segment) => (
                <mesh key={segment.color} rotation={[0, 0, segment.start]}>
                    <torusGeometry args={[0.42, 0.09, 12, 24, segment.length]} />
                    <meshStandardMaterial
                        color={segment.color}
                        emissive={segment.glow}
                        emissiveIntensity={0.4}
                        metalness={0.35}
                        roughness={0.3}
                    />
                </mesh>
            ))}
        </group>
    );
}

function Calendars() {
    const ref = useRef<Group>(null);
    const tiles = useMemo(
        () => [
            { label: "12", hot: false },
            { label: "28", hot: true },
            { label: "05", hot: false },
        ],
        [],
    );
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        group.children.forEach((child, index) => {
            child.position.y = 0.55 + Math.sin(t * 1.1 + index) * 0.08;
            child.rotation.y = Math.sin(t * 0.7 + index) * 0.4;
        });
    });
    return (
        <group ref={ref} position={[1.35, 0.95, 0.1]}>
            {tiles.map((tile, index) => (
                <mesh key={tile.label} position={[(index - 1) * 0.48, 0.55, 0]}>
                    <boxGeometry args={[0.28, 0.32, 0.05]} />
                    <meshStandardMaterial
                        color={tile.hot ? "#1FAE86" : "#24345C"}
                        emissive={tile.hot ? "#0C5C48" : "#121A33"}
                        emissiveIntensity={0.4}
                        metalness={0.3}
                        roughness={0.4}
                    />
                </mesh>
            ))}
        </group>
    );
}

function Arrows() {
    const ref = useRef<Group>(null);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        group.children.forEach((child, index) => {
            const along = Math.sin(t * 0.9 + index * 2);
            child.position.y = (index === 0 ? 0.15 : -0.05) + along * 0.22;
            child.rotation.z = index === 0 ? -0.35 : Math.PI + 0.35;
        });
    });
    return (
        <group ref={ref} position={[0.95, -1.05, 0.4]}>
            <group position={[-0.28, 0.15, 0]}>
                <Arrow color="#1FAE86" />
            </group>
            <group position={[0.28, -0.05, 0]} rotation={[0, 0, Math.PI]}>
                <Arrow color="#E07A68" />
            </group>
        </group>
    );
}

function Arrow({ color }: { color: string }) {
    return (
        <group>
            <mesh position={[0, -0.12, 0]}>
                <cylinderGeometry args={[0.035, 0.035, 0.28, 10]} />
                <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} metalness={0.6} roughness={0.25} />
            </mesh>
            <mesh position={[0, 0.08, 0]}>
                <coneGeometry args={[0.09, 0.16, 12]} />
                <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.35} metalness={0.6} roughness={0.25} />
            </mesh>
        </group>
    );
}

function Nodes() {
    const ref = useRef<Group>(null);
    useFrame(() => {
        const group = ref.current;
        if (!group) return;
        const t = sceneClock.t;
        group.children.forEach((child, index) => {
            const progress = (t * 0.25 + index * 0.18) % 1;
            const angle = index * 1.2 + progress * 2;
            child.position.set(Math.cos(angle) * (1.1 - progress * 0.7), Math.sin(angle) * 0.45, -0.7 + progress * 0.3);
        });
    });
    return (
        <group ref={ref}>
            {Array.from({ length: 6 }, (_, index) => (
                <mesh key={index}>
                    <sphereGeometry args={[0.045, 12, 12]} />
                    <meshStandardMaterial color="#8FB4FF" emissive="#2F6BFF" emissiveIntensity={0.6} />
                </mesh>
            ))}
        </group>
    );
}

type Pose = { x: number; y: number; z: number; rx: number; ry: number };

function lerpPose(a: Pose, b: Pose, amount: number): Pose {
    return {
        x: a.x + (b.x - a.x) * amount,
        y: a.y + (b.y - a.y) * amount,
        z: a.z + (b.z - a.z) * amount,
        rx: a.rx + (b.rx - a.rx) * amount,
        ry: a.ry + (b.ry - a.ry) * amount,
    };
}

function wave(value: number) {
    const triangle = value - Math.floor(value);
    const shaped = triangle < 0.5 ? triangle * 2 : (1 - triangle) * 2;
    return shaped * shaped * (3 - 2 * shaped);
}

function canvasTexture(draw: (context: CanvasRenderingContext2D, size: number) => void, size: number) {
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size === 1024 ? 644 : size;
    const context = canvas.getContext("2d");
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.anisotropy = 8;
    if (context) draw(context, size);
    texture.needsUpdate = true;
    return texture;
}

function cardFace(stops: string[], hologram: boolean, network: "visa" | "rupay", last4: string) {
    return canvasTexture((context) => {
        const gradient = context.createLinearGradient(0, 0, 1024, 644);
        stops.forEach((color, index) => gradient.addColorStop(index / (stops.length - 1), color));
        roundedRect(context, 0, 0, 1024, 644, 64);
        context.fillStyle = gradient;
        context.fill();
        context.strokeStyle = "rgba(255,255,255,0.08)";
        context.lineWidth = 2;
        for (let line = -2; line < 14; line += 1) {
            context.beginPath();
            context.moveTo(line * 90, 644);
            context.lineTo(line * 90 + 280, 0);
            context.stroke();
        }
        const sheen = context.createLinearGradient(200, 0, 820, 644);
        sheen.addColorStop(0, "rgba(255,255,255,0)");
        sheen.addColorStop(0.48, "rgba(255,255,255,0.18)");
        sheen.addColorStop(0.58, "rgba(255,255,255,0)");
        context.fillStyle = sheen;
        context.fillRect(0, 0, 1024, 644);
        if (hologram) {
            const holo = context.createLinearGradient(680, 80, 980, 420);
            holo.addColorStop(0, "rgba(112,231,255,0.55)");
            holo.addColorStop(0.5, "rgba(155,140,255,0.4)");
            holo.addColorStop(1, "rgba(232,196,119,0.45)");
            context.fillStyle = holo;
            roundedRect(context, 760, 300, 180, 120, 28);
            context.fill();
        }
        context.fillStyle = "#F7FAFF";
        context.font = "600 58px Poppins, sans-serif";
        context.fillText("FINQON", 78, 118);
        context.font = "500 52px Poppins, sans-serif";
        context.fillStyle = "rgba(247,250,255,0.92)";
        context.fillText(`••••    ${last4}`, 78, 548);
        if (network === "visa") drawVisa(context, 760, 500);
        else drawRupay(context, 748, 498);
        context.strokeStyle = "rgba(247,250,255,0.88)";
        context.lineWidth = 7;
        context.lineCap = "round";
        for (let arc = 0; arc < 3; arc += 1) {
            context.beginPath();
            context.arc(900, 118, 22 + arc * 18, -0.9, 0.9);
            context.stroke();
        }
    }, 1024);
}

function drawVisa(context: CanvasRenderingContext2D, x: number, y: number) {
    context.save();
    context.translate(x, y);
    context.shadowColor = "rgba(0,0,0,0.28)";
    context.shadowBlur = 10;
    context.shadowOffsetY = 3;
    roundedRect(context, 0, 0, 196, 78, 12);
    context.fillStyle = "#F4F7FF";
    context.fill();
    context.shadowColor = "transparent";
    context.clip();
    context.fillStyle = "#F7B600";
    context.fillRect(0, 60, 196, 18);
    context.font = "italic 800 46px Poppins, sans-serif";
    context.fillStyle = "#1A1F71";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText("VISA", 98, 32);
    context.restore();
}

function drawRupay(context: CanvasRenderingContext2D, x: number, y: number) {
    context.save();
    context.translate(x, y);
    context.shadowColor = "rgba(0,0,0,0.28)";
    context.shadowBlur = 10;
    context.shadowOffsetY = 3;
    roundedRect(context, 0, 0, 214, 82, 12);
    context.fillStyle = "#F7FAFF";
    context.fill();
    context.shadowColor = "transparent";
    context.clip();
    context.font = "700 40px Poppins, sans-serif";
    context.textBaseline = "middle";
    context.textAlign = "left";
    context.fillStyle = "#F37021";
    context.fillText("Ru", 18, 34);
    const ruWidth = context.measureText("Ru").width;
    context.fillStyle = "#09793E";
    context.fillText("Pay", 18 + ruWidth - 2, 34);
    context.strokeStyle = "#F37021";
    context.lineWidth = 4;
    context.lineCap = "round";
    context.beginPath();
    context.moveTo(22, 58);
    context.quadraticCurveTo(108, 74, 190, 56);
    context.stroke();
    context.strokeStyle = "#0A4EA3";
    context.lineWidth = 3;
    context.beginPath();
    context.moveTo(36, 66);
    context.quadraticCurveTo(112, 80, 186, 64);
    context.stroke();
    context.restore();
}

function cardBack(color: string) {
    return canvasTexture((context) => {
        context.fillStyle = color;
        context.fillRect(0, 0, 1024, 644);
        context.fillStyle = "#0a0c12";
        context.fillRect(0, 150, 1024, 110);
        context.fillStyle = "rgba(247,250,255,0.75)";
        context.font = "600 36px Poppins, sans-serif";
        context.fillText("FINQON", 78, 520);
        context.font = "500 28px Poppins, sans-serif";
        context.fillStyle = "rgba(247,250,255,0.55)";
        context.fillText("AUTHORIZED SIGNATURE", 78, 430);
    }, 1024);
}

function coinMaterials(symbol: string, color: string) {
    const map = canvasTexture((context, size) => {
        const glow = context.createRadialGradient(size * 0.38, size * 0.34, 20, size / 2, size / 2, size / 2);
        glow.addColorStop(0, "#fffaf0");
        glow.addColorStop(0.45, color);
        glow.addColorStop(1, color);
        context.fillStyle = glow;
        context.beginPath();
        context.arc(size / 2, size / 2, size / 2 - 4, 0, Math.PI * 2);
        context.fill();
        context.strokeStyle = "rgba(255,255,255,0.85)";
        context.lineWidth = 16;
        context.stroke();
        context.beginPath();
        context.arc(size / 2, size / 2, size / 2 - 48, 0, Math.PI * 2);
        context.lineWidth = 5;
        context.strokeStyle = "rgba(70,48,16,0.35)";
        context.stroke();
        context.fillStyle = "#3a2a12";
        context.font = "700 220px Poppins, sans-serif";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(symbol, size / 2, size / 2 + 14);
    }, 512);
    const face = new MeshStandardMaterial({ map, metalness: 0.92, roughness: 0.12, emissive: color, emissiveIntensity: 0.18 });
    const rim = new MeshStandardMaterial({ color, metalness: 1, roughness: 0.1, emissive: color, emissiveIntensity: 0.22 });
    return [rim, face, face];
}

function roundedRect(context: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
    context.beginPath();
    context.moveTo(x + radius, y);
    context.arcTo(x + width, y, x + width, y + height, radius);
    context.arcTo(x + width, y + height, x, y + height, radius);
    context.arcTo(x, y + height, x, y, radius);
    context.arcTo(x, y, x + width, y, radius);
    context.closePath();
}

function faceTexture() {
    return canvasTexture((context, size) => {
        context.clearRect(0, 0, size, size);
        context.fillStyle = "#1B2A44";
        roundedRect(context, size * 0.06, size * 0.18, size * 0.88, size * 0.64, 36);
        context.fill();
        context.fillStyle = "#8EEBFF";
        context.beginPath();
        context.ellipse(size * 0.36, size * 0.46, 22, 34, 0, 0, Math.PI * 2);
        context.ellipse(size * 0.64, size * 0.46, 22, 34, 0, 0, Math.PI * 2);
        context.fill();
        context.strokeStyle = "#7DEEC8";
        context.lineWidth = 8;
        context.lineCap = "round";
        context.beginPath();
        context.arc(size / 2, size * 0.62, 28, 0.25, Math.PI - 0.25);
        context.stroke();
    }, 256);
}

function speechTexture() {
    return canvasTexture((context, size) => {
        context.clearRect(0, 0, size, size);
        const bodyY = 28;
        const bodyH = size * 0.66;
        const gradient = context.createLinearGradient(0, bodyY, size, bodyY + bodyH);
        gradient.addColorStop(0, "#3D7CFF");
        gradient.addColorStop(1, "#1A4FD0");
        context.fillStyle = gradient;
        roundedRect(context, 36, bodyY, size - 72, bodyH, 78);
        context.fill();
        context.beginPath();
        context.moveTo(size * 0.24, bodyY + bodyH - 6);
        context.lineTo(size * 0.1, size * 0.94);
        context.lineTo(size * 0.42, bodyY + bodyH - 6);
        context.fill();
        context.fillStyle = "#F7FAFF";
        const dotY = bodyY + bodyH * 0.46;
        [0.38, 0.5, 0.62].forEach((spot) => {
            context.beginPath();
            context.arc(size * spot, dotY, 20, 0, Math.PI * 2);
            context.fill();
        });
    }, 512);
}

function symbolTexture(symbol: string, color: string) {
    return canvasTexture((context, size) => {
        context.clearRect(0, 0, size, size);
        context.fillStyle = color;
        context.font = "700 280px Poppins, sans-serif";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(symbol, size / 2, size / 2 + 16);
    }, 256);
}

function StaticStage() {
    return (
        <div className="relative h-full w-full overflow-hidden bg-[#05070F]">
            <div className="absolute left-[22%] top-[30%] h-16 w-16 rounded-full bg-[#E7F2FF]" />
            <div className="absolute left-[20%] top-[46%] h-20 w-14 rounded-full bg-[#7DEEC8]" />
            <div className="absolute left-[18%] top-[28%] h-36 w-56 -rotate-12 rounded-2xl border border-white/20 bg-gradient-to-br from-[#16315f] via-[#315BEA] to-[#101735] shadow-[0_20px_50px_rgba(49,91,234,0.35)]">
                <div className="absolute left-4 top-4 h-6 w-8 rounded-sm bg-[#E8C477]" />
                <p className="absolute bottom-4 left-4 text-sm font-medium tracking-wide text-[#F7FAFF]">FINQON  •••• 4821</p>
            </div>
            <div className="absolute right-[16%] top-[22%] flex h-14 w-14 items-center justify-center rounded-full border-4 border-[#f3e2b4] bg-[#E8C477] text-xl font-semibold text-[#1a1408] shadow-lg">
                ₹
            </div>
            <div className="absolute bottom-[24%] right-[28%] flex h-12 w-12 items-center justify-center rounded-full border-4 border-white/70 bg-[#DCE3F1] text-lg font-semibold text-[#1a1408]">
                $
            </div>
        </div>
    );
}
