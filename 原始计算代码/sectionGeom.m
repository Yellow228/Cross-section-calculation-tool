function [A,P,B] = sectionGeom(x,z,H)
%水流流量关系曲线函数-------
A = 0; 
P = 0; 
B = 0;

for i = 1:length(x)-1

    x1 = x(i);   x2 = x(i+1);
    z1 = z(i);   z2 = z(i+1);

    % -------- 全在水上 --------
    if H <= min(z1,z2)
        continue
    end

    % -------- 全在水下 --------
    if H >= max(z1,z2)

        h1 = H - z1;
        h2 = H - z2;

        dx = abs(x2-x1);

        A = A + (h1 + h2)/2 * dx;
        P = P + sqrt(dx^2 + (z2-z1)^2);
        B = B + dx;

    else
        % -------- 部分浸没 → 插值 --------

        xi = x1 + (x2-x1)*(H-z1)/(z2-z1);

        if z1 < H     % 点1在水下

            dx = abs(xi - x1);
            h1 = H - z1;

            A = A + h1/2 * dx;
            P = P + sqrt(dx^2 + h1^2);
            B = B + dx;

        else         % 点2在水下

            dx = abs(x2 - xi);
            h2 = H - z2;

            A = A + h2/2 * dx;
            P = P + sqrt(dx^2 + h2^2);
            B = B + dx;

        end
    end
end
end

