from manim import *
from manim import config

config.frame_rate = 24

class ExplainerScene(Scene):
    def construct(self):

        # ─────────────────────────────────────────────
        # BEAT 1 : Title card
        # ─────────────────────────────────────────────
        title = Text("Newton's First Law of Motion", font_size=48, color=YELLOW)
        subtitle = Text("An object at rest stays at rest,\nan object in motion stays in motion\n— unless acted upon by an external force.",
                        font_size=28, color=WHITE)
        subtitle.next_to(title, DOWN, buff=0.5)

        self.play(Write(title), run_time=2)
        self.play(FadeIn(subtitle, shift=UP*0.3), run_time=2)
        self.wait(3)
        self.play(FadeOut(title), FadeOut(subtitle))

        # ─────────────────────────────────────────────
        # BEAT 2 : Object at REST — no force, no movement
        # ─────────────────────────────────────────────
        beat2_title = Text("Part 1 : Object at Rest", font_size=36, color=YELLOW)
        beat2_title.to_edge(UP, buff=0.4)

        # Floor
        floor = Line(LEFT*6, RIGHT*6, color=WHITE, stroke_width=3).shift(DOWN*2)

        # Ball sitting on floor
        ball = Circle(radius=0.4, color=BLUE_C, fill_opacity=1)
        ball.move_to(LEFT*3 + DOWN*1.6)

        rest_label = Text("Object at REST", font_size=32, color=WHITE)
        rest_label.next_to(ball, UP, buff=0.5)

        no_force_label = Text("No external force → No movement", font_size=28, color=RED_C)
        no_force_label.to_edge(DOWN, buff=0.6)

        self.play(FadeIn(beat2_title))
        self.play(Create(floor), run_time=1)
        self.play(GrowFromCenter(ball), run_time=1)
        self.play(Write(rest_label))
        self.wait(1)

        # Show tiny cross / "X" marks to indicate no force
        x_left  = Text("✕", font_size=32, color=RED_C).next_to(ball, LEFT,  buff=0.6)
        x_right = Text("✕", font_size=32, color=RED_C).next_to(ball, RIGHT, buff=0.6)
        self.play(FadeIn(x_left), FadeIn(x_right))
        self.play(Write(no_force_label))
        self.wait(3)

        self.play(*[FadeOut(m) for m in [beat2_title, floor, ball, rest_label,
                                          no_force_label, x_left, x_right]])

        # ─────────────────────────────────────────────
        # BEAT 3 : A FORCE is applied — ball starts moving
        # ─────────────────────────────────────────────
        beat3_title = Text("Part 2 : Force Applied → Object Moves", font_size=36, color=YELLOW)
        beat3_title.to_edge(UP, buff=0.4)

        floor2 = Line(LEFT*6, RIGHT*6, color=WHITE, stroke_width=3).shift(DOWN*2)

        ball2 = Circle(radius=0.4, color=BLUE_C, fill_opacity=1)
        ball2.move_to(LEFT*4 + DOWN*1.6)

        # Push arrow (force)
        push_arrow = Arrow(start=LEFT*5.8 + DOWN*1.6,
                           end=LEFT*4.6 + DOWN*1.6,
                           color=GREEN_C, buff=0, stroke_width=6)
        push_label = Text("Force", font_size=28, color=GREEN_C)
        push_label.next_to(push_arrow, UP, buff=0.15)

        self.play(FadeIn(beat3_title))
        self.play(Create(floor2), GrowFromCenter(ball2), run_time=1)
        self.wait(0.5)
        self.play(GrowArrow(push_arrow), Write(push_label), run_time=1)
        self.wait(0.5)

        # Ball accelerates to the right
        vel_arrow = Arrow(start=ball2.get_right(),
                          end=ball2.get_right() + RIGHT*1.4,
                          color=GREEN_C, buff=0, stroke_width=5)
        vel_label = Text("Velocity", font_size=24, color=GREEN_C)
        vel_label.next_to(vel_arrow, UP, buff=0.1)

        self.play(FadeOut(push_arrow), FadeOut(push_label),
                  ball2.animate.shift(RIGHT*5.5), run_time=2)
        self.play(GrowArrow(vel_arrow), Write(vel_label), run_time=0.8)
        self.wait(2)

        self.play(*[FadeOut(m) for m in [beat3_title, floor2, ball2,
                                          vel_arrow, vel_label]])

        # ─────────────────────────────────────────────
        # BEAT 4 : Ball in motion — NO friction → keeps going forever
        # ─────────────────────────────────────────────
        beat4_title = Text("Part 3 : No Friction → Constant Velocity", font_size=36, color=YELLOW)
        beat4_title.to_edge(UP, buff=0.4)

        # Space / frictionless surface label
        space_label = Text("Frictionless Surface (No friction)", font_size=28, color=BLUE_C)
        space_label.shift(DOWN*2.6)

        floor3 = DashedLine(LEFT*6, RIGHT*6, color=BLUE_C,
                             dash_length=0.25, stroke_width=2).shift(DOWN*2)

        ball3 = Circle(radius=0.4, color=BLUE_C, fill_opacity=1)
        ball3.move_to(LEFT*5 + DOWN*1.6)

        const_vel_arrow = Arrow(start=ball3.get_right(),
                                end=ball3.get_right() + RIGHT*1.2,
                                color=GREEN_C, buff=0, stroke_width=5)
        const_vel_label = Text("Constant Velocity", font_size=24, color=GREEN_C)
        const_vel_label.next_to(const_vel_arrow, UP, buff=0.1)

        no_friction_lbl = Text("No opposing force → motion continues!", font_size=28, color=WHITE)
        no_friction_lbl.to_edge(DOWN, buff=0.6)

        self.play(FadeIn(beat4_title))
        self.play(Create(floor3), GrowFromCenter(ball3), FadeIn(space_label), run_time=1.2)

        # Add velocity arrow that travels with ball
        arrow_group = VGroup(const_vel_arrow, const_vel_label)
        arrow_group.shift(LEFT*5 + DOWN*1.6 - ball3.get_center() + RIGHT*0.4)

        self.play(GrowArrow(const_vel_arrow), Write(const_vel_label), run_time=0.8)
        self.wait(0.5)
        self.play(Write(no_friction_lbl))

        # Ball moves at constant speed all the way across
        self.play(
            ball3.animate.shift(RIGHT*10),
            arrow_group.animate.shift(RIGHT*10),
            run_time=4,
            rate_func=linear
        )
        self.wait(2)

        self.play(*[FadeOut(m) for m in [beat4_title, floor3, ball3,
                                          arrow_group, space_label, no_friction_lbl]])

        # ─────────────────────────────────────────────
        # BEAT 5 : Friction (opposing force) — ball slows and stops
        # ─────────────────────────────────────────────
        beat5_title = Text("Part 4 : Friction Acts → Object Slows & Stops", font_size=36, color=YELLOW)
        beat5_title.to_edge(UP, buff=0.4)

        floor4 = Line(LEFT*6, RIGHT*6, color=WHITE, stroke_width=3).shift(DOWN*2)

        ball4 = Circle(radius=0.4, color=BLUE_C, fill_opacity=1)
        ball4.move_to(LEFT*3.5 + DOWN*1.6)

        fwd_arrow = Arrow(start=ball4.get_right(),
                          end=ball4.get_right() + RIGHT*1.3,
                          color=GREEN_C, buff=0, stroke_width=5)
        fwd_label = Text("Velocity", font_size=24, color=GREEN_C)
        fwd_label.next_to(fwd_arrow, UP, buff=0.1)

        fric_arrow = Arrow(start=ball4.get_left(),
                           end=ball4.get_left() + LEFT*1.1,
                           color=RED_C, buff=0, stroke_width=5)
        fric_label = Text("Friction (opposing force)", font_size=24, color=RED_C)
        fric_label.next_to(fric_arrow, DOWN, buff=0.15)

        stops_label = Text("Ball slows down and STOPS", font_size=32, color=RED_C)
        stops_label.to_edge(DOWN, buff=0.6)

        self.play(FadeIn(beat5_title))
        self.play(Create(floor4), GrowFromCenter(ball4), run_time=1)
        self.play(GrowArrow(fwd_arrow), Write(fwd_label), run_time=0.7)
        self.play(GrowArrow(fric_arrow), Write(fric_label), run_time=0.7)
        self.wait(1)
        self.play(Write(stops_label))

        # Ball decelerates to a stop
        self.play(
            ball4.animate.shift(RIGHT*1.8),
            fwd_arrow.animate.set_opacity(0.2),
            run_time=2.5,
            rate_func=lambda t: smooth(t) * (1 - t)
        )
        self.wait(2)

        self.play(*[FadeOut(m) for m in [beat5_title, floor4, ball4,
                                          fwd_arrow, fwd_label,
                                          fric_arrow, fric_label, stops_label]])

        # ─────────────────────────────────────────────
        # BEAT 6 : Summary
        # ─────────────────────────────────────────────
        summary_title = Text("Summary : Newton's First Law", font_size=40, color=YELLOW)
        summary_title.to_edge(UP, buff=0.4)

        lines = [
            Text("1. An object at REST stays at rest", font_size=30, color=WHITE),
            Text("   — unless a force acts on it.", font_size=28, color=BLUE_C),
            Text("2. An object in MOTION stays in motion", font_size=30, color=WHITE),
            Text("   — at constant velocity, in a straight line.", font_size=28, color=BLUE_C),
            Text("3. Friction is an external force that stops motion.", font_size=28, color=RED_C),
            Text("This tendency is called  INERTIA.", font_size=34, color=YELLOW),
        ]

        summary_group = VGroup(*lines).arrange(DOWN, aligned_edge=LEFT, buff=0.35)
        summary_group.next_to(summary_title, DOWN, buff=0.5)

        self.play(FadeIn(summary_title))
        for line in lines:
            self.play(Write(line), run_time=1)
        self.wait(4)

        self.play(*[FadeOut(m) for m in [summary_title, *lines]])