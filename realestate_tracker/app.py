from flask import Flask, redirect, render_template, request, url_for, flash

from .config import Config
from .extensions import db
from .models import Criteria, Property, Recommendation, PROPERTY_TYPES, STRATEGIES
from .ingestion.pipeline import run_ingestion


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    # Flask's app.config is dict-style; scoring/ingestion code uses
    # attribute-style access on the Config class itself, so keep a direct
    # reference to it rather than threading app.config through everywhere.
    app.re_config = config_class
    db.init_app(app)

    with app.app_context():
        db.create_all()

    register_routes(app)
    return app


def register_routes(app):

    @app.route("/")
    def dashboard():
        location = request.args.get("location", "").strip()
        property_type = request.args.get("property_type", "")
        zoning = request.args.get("zoning", "").strip()
        min_cap_rate = request.args.get("min_cap_rate", type=float)
        max_price = request.args.get("max_price", type=float)
        strategy = request.args.get("strategy", "")
        sort = request.args.get("sort", "recommended")

        query = Recommendation.query.join(Property).filter(Recommendation.status != "dismissed")

        if location:
            like = f"%{location}%"
            query = query.filter(
                db.or_(Property.city.ilike(like), Property.zip_code.ilike(like), Property.state.ilike(like))
            )
        if property_type:
            query = query.filter(Property.property_type == property_type)
        if zoning:
            query = query.filter(Property.zoning_code.ilike(f"{zoning}%"))
        if min_cap_rate is not None:
            query = query.filter(Property.cap_rate >= min_cap_rate / 100)
        if max_price is not None:
            query = query.filter(Property.list_price <= max_price)
        if strategy:
            query = query.filter(Property.recommended_strategy == strategy)

        if sort == "cap_rate":
            query = query.order_by(Property.cap_rate.desc())
        elif sort == "price_asc":
            query = query.order_by(Property.list_price.asc())
        elif sort == "price_desc":
            query = query.order_by(Property.list_price.desc())
        elif sort == "newest":
            query = query.order_by(Recommendation.created_at.desc())
        else:
            query = query.order_by(Recommendation.match_score.desc())

        recommendations = query.limit(100).all()
        new_count = Recommendation.query.filter_by(status="new").count()
        total_properties = Property.query.count()
        active_criteria_count = Criteria.query.filter_by(active=True).count()

        return render_template(
            "dashboard.html",
            recommendations=recommendations,
            property_types=PROPERTY_TYPES,
            strategies=STRATEGIES,
            filters=request.args,
            new_count=new_count,
            total_properties=total_properties,
            active_criteria_count=active_criteria_count,
        )

    @app.route("/properties/<int:property_id>")
    def property_detail(property_id):
        prop = Property.query.get_or_404(property_id)
        recs = Recommendation.query.filter_by(property_id=prop.id).all()
        return render_template("property_detail.html", prop=prop, recommendations=recs)

    @app.route("/recommendations/<int:rec_id>/status", methods=["POST"])
    def update_recommendation_status(rec_id):
        rec = Recommendation.query.get_or_404(rec_id)
        new_status = request.form.get("status")
        if new_status in {"new", "viewed", "saved", "dismissed"}:
            rec.status = new_status
            db.session.commit()
            flash(f"Marked as {new_status}.", "success")
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/criteria")
    def criteria_list():
        all_criteria = Criteria.query.order_by(Criteria.created_at.desc()).all()
        return render_template(
            "criteria.html", all_criteria=all_criteria, property_types=PROPERTY_TYPES, strategies=STRATEGIES
        )

    @app.route("/criteria/new", methods=["POST"])
    def criteria_create():
        form = request.form
        criteria = Criteria(
            name=form.get("name") or "Untitled search",
            min_price=form.get("min_price", type=float),
            max_price=form.get("max_price", type=float),
            min_cap_rate=(form.get("min_cap_rate", type=float) or 0) / 100 or None,
            min_lot_size_sqft=form.get("min_lot_size_sqft", type=float),
            strategy=form.get("strategy") or "both",
            active=True,
        )
        criteria.location_list = [loc.strip() for loc in form.get("locations", "").split(",") if loc.strip()]
        criteria.property_type_list = form.getlist("property_types")
        criteria.zoning_code_list = [z.strip() for z in form.get("zoning_codes", "").split(",") if z.strip()]

        db.session.add(criteria)
        db.session.commit()
        flash(f"Saved search '{criteria.name}' created.", "success")
        return redirect(url_for("criteria_list"))

    @app.route("/criteria/<int:criteria_id>/toggle", methods=["POST"])
    def criteria_toggle(criteria_id):
        criteria = Criteria.query.get_or_404(criteria_id)
        criteria.active = not criteria.active
        db.session.commit()
        return redirect(url_for("criteria_list"))

    @app.route("/criteria/<int:criteria_id>/delete", methods=["POST"])
    def criteria_delete(criteria_id):
        criteria = Criteria.query.get_or_404(criteria_id)
        Recommendation.query.filter_by(criteria_id=criteria.id).delete()
        db.session.delete(criteria)
        db.session.commit()
        flash("Saved search deleted.", "success")
        return redirect(url_for("criteria_list"))

    @app.route("/ingest", methods=["POST"])
    def trigger_ingest():
        summary = run_ingestion(app.re_config)
        flash(
            f"Checked for new listings: {summary['fetched']} fetched, "
            f"{summary['new']} new, {summary['recommendations']} new recommendations.",
            "success",
        )
        return redirect(url_for("dashboard"))

    @app.route("/api/properties")
    def api_properties():
        properties = Property.query.order_by(Property.cap_rate.desc()).limit(200).all()
        return {"properties": [p.to_dict() for p in properties]}


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5050)
