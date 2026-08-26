using UnityEngine;

public class CollisionReporter : MonoBehaviour
{
    public RobotSocketReceiver receiver;

    void OnCollisionEnter(Collision collision)
    {
        if (receiver != null)
            receiver.ReportCollision(collision);
    }
}
