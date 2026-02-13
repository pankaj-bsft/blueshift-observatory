"""
Health Score Service
Calculates email deliverability health scores based on key metrics
"""


def calculate_delivery_score(delivery_rate: float) -> int:
    """Calculate score based on delivery rate (max 25 points)"""
    if delivery_rate >= 98:
        return 25
    elif delivery_rate >= 96:
        return 22
    elif delivery_rate >= 94:
        return 20
    elif delivery_rate >= 92:
        return 15
    elif delivery_rate >= 90:
        return 10
    else:
        return 5


def calculate_bounce_score(bounce_rate: float) -> int:
    """Calculate score based on bounce rate (max 25 points)"""
    if bounce_rate <= 0.5:
        return 25
    elif bounce_rate <= 0.9:
        return 20
    elif bounce_rate <= 1.5:
        return 15
    else:
        return 5


def calculate_open_score(open_rate: float) -> int:
    """Calculate score based on open rate (max 25 points)"""
    if open_rate >= 12:
        return 25
    elif open_rate >= 10:
        return 20
    elif open_rate >= 8:
        return 15
    elif open_rate >= 6:
        return 10
    else:
        return 5


def calculate_unsub_score(unsub_rate: float) -> int:
    """Calculate score based on unsubscribe rate (max 10 points)"""
    if unsub_rate <= 0.1:
        return 10
    elif unsub_rate <= 0.39:
        return 7
    elif unsub_rate <= 0.99:
        return 5
    else:
        return 2


def calculate_spam_score(spam_rate: float) -> int:
    """Calculate score based on spam rate (max 15 points)"""
    if 0.01 <= spam_rate <= 0.02:
        return 15
    elif 0.03 <= spam_rate <= 0.04:
        return 10
    elif 0.05 <= spam_rate <= 0.09:
        return 5
    else:
        return 2


def calculate_health_score(summary: dict) -> dict:
    """
    Calculate overall health score based on deliverability metrics

    Args:
        summary: Dictionary with metrics (Delivery_Rate_%, Bounce_Rate_%, etc.)

    Returns:
        Dictionary with score breakdown and total (max 100 points)
    """
    if not summary:
        return {
            'total_score': 0,
            'delivery_score': 0,
            'bounce_score': 0,
            'open_score': 0,
            'unsub_score': 0,
            'spam_score': 0,
            'health_rating': 'N/A'
        }

    # Calculate individual scores
    delivery_score = calculate_delivery_score(summary.get('Delivery_Rate_%', 0))
    bounce_score = calculate_bounce_score(summary.get('Bounce_Rate_%', 0))
    open_score = calculate_open_score(summary.get('Open_Rate_%', 0))
    unsub_score = calculate_unsub_score(summary.get('Unsub_Rate_%', 0))
    spam_score = calculate_spam_score(summary.get('Spam_Rate_%', 0))

    # Total score (max 100)
    total_score = delivery_score + bounce_score + open_score + unsub_score + spam_score

    # Determine health rating
    if total_score >= 85:
        health_rating = 'Excellent'
    elif total_score >= 70:
        health_rating = 'Good'
    elif total_score >= 55:
        health_rating = 'Fair'
    elif total_score >= 40:
        health_rating = 'Poor'
    else:
        health_rating = 'Critical'

    return {
        'total_score': total_score,
        'delivery_score': delivery_score,
        'bounce_score': bounce_score,
        'open_score': open_score,
        'unsub_score': unsub_score,
        'spam_score': spam_score,
        'health_rating': health_rating
    }


def add_health_score_to_summary(summary: dict) -> dict:
    """
    Add health score to a summary dictionary

    Args:
        summary: Summary dictionary with metrics

    Returns:
        Updated summary with Health_Score and Health_Rating
    """
    if not summary:
        return summary

    health_data = calculate_health_score(summary)
    summary['Health_Score'] = health_data['total_score']
    summary['Health_Rating'] = health_data['health_rating']
    summary['Health_Score_Breakdown'] = {
        'delivery': health_data['delivery_score'],
        'bounce': health_data['bounce_score'],
        'open': health_data['open_score'],
        'unsub': health_data['unsub_score'],
        'spam': health_data['spam_score']
    }

    return summary
